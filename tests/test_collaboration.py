import asyncio
import json
import time

from fastapi.testclient import TestClient
from test_engine import Clock

from band.api import create_app
from band.music import PPQ, TRACKS, ConductorPlan, MusicSettings
from band.negotiation import BassNegotiator, Reply
from band.planner import Planner
from band.timing import Scheduler


def test_three_comparison_modes_and_peer_replies():
    async def measure():
        planner = Planner()
        captures = []
        plans = {}
        for mode in ("solo", "shared", "negotiate"):
            settings = MusicSettings(
                collaboration=mode,
                density=0.9,
                conductor=ConductorPlan(style="funk"),
            )
            plans[mode] = await planner.plan(1, settings, 7, on_negotiation=captures.append)
        return plans, captures

    plans, captures = asyncio.run(measure())
    assert not plans["solo"].negotiation and not plans["shared"].negotiation
    assert tuple(captures) == plans["negotiate"].negotiation
    assert {part.sender for part in captures if part.stage == "proposal"} == TRACKS.keys()
    assert all(
        a.proposal_id == b.proposal_id
        and a.stage == "proposal"
        and b.stage in {"accepted", "declined"}
        for a, b in zip(captures[::2], captures[1::2], strict=True)
    )
    assert plans["solo"].notes != plans["shared"].notes
    assert plans["shared"].notes != plans["negotiate"].notes


class SlowBass(BassNegotiator):
    async def respond(self, offer, notes, settings):
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            await asyncio.sleep(0.08)
        return await super().respond(offer, notes, settings)


def test_peer_agreement_cannot_change_pitch():
    class InvalidBass(BassNegotiator):
        async def respond(self, offer, notes, settings):
            first = notes[0].model_copy(update={"pitch": notes[0].pitch + 1})
            return Reply("改写和弦", (first, *notes[1:]))

    async def check():
        planner = Planner(participants={"bass": InvalidBass()})
        settings = MusicSettings()
        negotiated = await planner.plan(0, settings, 0)
        shared = await planner.plan(0, settings.model_copy(update={"collaboration": "shared"}), 0)
        assert negotiated.negotiation[1].stage == "declined"
        assert tuple(n for n in negotiated.notes if n.track_id == "bass") == tuple(
            n for n in shared.notes if n.track_id == "bass"
        )

    asyncio.run(check())


def test_slow_peer_leaves_the_draft_playable_and_clock_running(tmp_path):
    def build():
        return Planner(participants={"bass": SlowBass()}, negotiation_deadline=0.045)

    def wait_until_bar(client, prefix, target):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            state = client.get(f"{prefix}/state").json()
            if state["bar"] >= target:
                return state
            time.sleep(0.025)
        raise AssertionError("Playback did not advance")

    with TestClient(
        create_app(tmp_path, planner_factory=build), base_url="http://localhost"
    ) as api:
        initial = api.post("/sessions", json={"bpm": 240}).json()
        prefix = f"/sessions/{initial['id']}"
        assert any(event["stage"] == "timeout" for event in initial["negotiation"])
        with api.websocket_connect(f"ws://localhost{prefix}/events") as socket:
            frame = socket.receive_json()
            assert frame["state"]["negotiation"] == initial["negotiation"]
            api.post(f"{prefix}/play")
            state = wait_until_bar(api, prefix, target=2)
            assert state["transport"] == "playing"
            plan = next(plan for plan in state["plans"] if plan["bar"] == state["bar"])
            assert not plan["fallback_tracks"]
            messages = plan["negotiation"]
            assert [(m["kind"], m["stage"]) for m in messages][:2] == [
                ("kick_pattern", "proposal"),
                ("kick_pattern", "timeout"),
            ]
            assert any(m["sender"] == "piano" for m in messages)
            api.post(f"{prefix}/stop")

    log = (tmp_path / "sessions" / f"{initial['id']}.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in log]
    for bar in (0, 1):
        assert {
            e["track_id"] for e in events if e["type"] == "note_on" and e["bar"] == bar
        } == TRACKS.keys()
    active = set()
    for event in events:
        if event["type"] == "note_on":
            assert event["event_id"] not in active
            active.add(event["event_id"])
        elif event["type"] == "note_off":
            active.remove(event["event_id"])
    assert not active


def test_musical_tempo_pivot_survives_missing_bar_plans():
    clock = Clock()
    scheduler = Scheduler(Planner(), clock=clock, threaded=False)
    try:
        scheduler.play()
        pending = scheduler.stage_settings({"bpm": 180, "conductor": {"style": "bossa"}})["pending"]
        assert pending["pickup_bar"] == 1 and pending["bar"] == 2
        while scheduler.snapshot()["current_bar"] < 3:
            clock.step(scheduler)
        events = scheduler.events_after(0)
        bars = [event for event in events if event["type"] == "bar"]
        assert [event["bpm"] for event in bars] == [120, 120, 180, 180]
        assert bars[1]["transition"]["phase"] == "pickup"
        assert bars[2]["transition"]["phase"] == "arrival"
        cue = [
            event
            for event in events
            if event["type"] == "note_on"
            and event["bar"] == 1
            and event["track_id"] == "drums"
            and event["event_id"].startswith("transition")
        ]
        assert [event["start_tick"] for event in cue] == [8 * PPQ // 3, 10 * PPQ // 3]
        assert abs(cue[1]["due_ns"] - cue[0]["due_ns"] - 1e9 / 3) <= 1
        assert abs(bars[2]["due_ns"] - cue[1]["due_ns"] - 1e9 / 3) <= 1
        landing = [
            event
            for event in events
            if event["type"] == "note_on" and event["bar"] == 2 and event["start_tick"] == 0
        ]
        assert {note["track_id"] for note in landing} == TRACKS.keys()
    finally:
        scheduler.close()


def test_late_redirect_keeps_locked_pickup_and_first_arrival():
    clock = Clock()
    scheduler = Scheduler(Planner(), clock=clock, threaded=False)
    try:
        scheduler.play()
        first = scheduler.stage_settings({"conductor": {"style": "bossa"}})["pending"]
        assert first["bar"] == 2
        rewritten = scheduler.stage_settings({"conductor": {"style": "funk"}})["pending"]
        assert rewritten["bar"] == 2 and rewritten["transition"]["to_style"] == "funk"
        while scheduler.snapshot()["locked_until_bar"] < 1:
            clock.step(scheduler)
        snapshot = scheduler.stage_settings({"conductor": {"style": "ballad"}})
        first, second = snapshot["pending_changes"]
        assert (first["pickup_bar"], first["bar"], first["transition"]["to_style"]) == (
            1,
            2,
            "funk",
        )
        assert (second["pickup_bar"], second["bar"], second["transition"]["from_style"]) == (
            3,
            4,
            "funk",
        )
    finally:
        scheduler.close()
