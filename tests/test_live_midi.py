import asyncio
import json
import time

from fastapi.testclient import TestClient
from test_engine import Clock, fill

from band.api import create_app
from band.music import PlayerPhrase
from band.planner import Planner
from band.timing import Scheduler

DEVICE = {"id": "virtual-keyboard", "name": "QA keyboard"}


class Keyboard:
    def __init__(self, message, status):
        self.message = message
        self.status = status
        self.selected = None

    def start(self):
        self.report("disabled")

    def select(self, port):
        self.selected = port
        self.report("connected" if port else "disabled")

    def report(self, connection):
        self.status(
            {
                "ports": [] if connection == "waiting" else [DEVICE],
                "selected": self.selected,
                "connection": connection,
                "error": None,
            }
        )

    def play(self, *packet):
        self.message(DEVICE["id"], packet)

    def close(self):
        pass


def until(api, prefix, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = api.get(f"{prefix}/state").json()
        if predicate(state):
            return state
        time.sleep(0.015)
    responses = [
        (plan["bar"], plan["player_response"] and plan["player_response"]["phrase"])
        for plan in state["plans"]
    ]
    raise AssertionError(
        f"Session state did not match at bar {state['bar']} ({state['transport']}): "
        f"midi={state['midi']} plans={responses}"
    )


def test_monitor_works_while_stopped_and_releases_sustained_keys(tmp_path):
    devices = []

    def build(message, status):
        devices.append(Keyboard(message, status))
        return devices[-1]

    with TestClient(create_app(tmp_path, midi_factory=build), base_url="http://localhost") as api:
        sid = api.post("/sessions").json()["id"]
        prefix = f"/sessions/{sid}"
        until(api, prefix, lambda s: s["midi"]["ports"] == [DEVICE])
        assert api.patch(f"{prefix}/midi", json={"volume": None}).status_code == 422
        api.patch(f"{prefix}/midi", json={"port": DEVICE["id"], "volume": 0.7, "program": 4})
        until(api, prefix, lambda s: s["midi"]["connection"] == "connected")
        keyboard = devices[0]
        with api.websocket_connect(f"ws://localhost{prefix}/events") as ws:
            ws.receive_json()
            keyboard.play(0x90, 60, 94)
            for _ in range(12):
                update = ws.receive_json()
                note = next((e for e in update["events"] if e["type"] == "midi_on"), None)
                if note:
                    break
            assert note and note["pitch"] == 60 and note["program"] == 4 and note["volume"] == 0.7
            assert update["state"]["transport"] == "stopped"
            keyboard.play(0xB0, 64, 127)
            keyboard.play(0x90, 60, 0)
            sustained = until(api, prefix, lambda s: s["midi"]["pressed"] == [])
            assert sustained["midi"]["sounding"] == [60]
            keyboard.play(0xB0, 64, 0)
            until(api, prefix, lambda s: not s["midi"]["sounding"])
            api.post(f"{prefix}/play")
            keyboard.play(0x90, 60, 88)
            until(api, prefix, lambda s: s["midi"]["sounding"] == [60])
            api.post(f"{prefix}/stop")
            assert api.get(f"{prefix}/state").json()["midi"]["sounding"] == [60]
            keyboard.play(0xB0, 123, 0)
            until(api, prefix, lambda s: not s["midi"]["sounding"])

    audit = (tmp_path / "sessions" / f"{sid}.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in audit]
    assert {e["event_id"] for e in events if e["type"] == "midi_on"} == {
        e["event_id"] for e in events if e["type"] == "midi_off"
    }


def test_keyboard_phrase_replans_and_port_loss_does_not_stop_transport(tmp_path):
    devices = []

    def build(message, status):
        devices.append(Keyboard(message, status))
        return devices[-1]

    with TestClient(create_app(tmp_path, midi_factory=build), base_url="http://localhost") as api:
        prefix = f"/sessions/{api.post('/sessions', json={'bpm': 240}).json()['id']}"
        api.patch(f"{prefix}/midi", json={"port": DEVICE["id"]})
        until(api, prefix, lambda s: s["midi"]["connection"] == "connected")
        keyboard = devices[0]
        assert api.post(f"{prefix}/play").json()["transport"] == "playing"
        for pitch in (75, 77, 79, 81, 83, 82):
            keyboard.play(0x90, pitch, 100)
            time.sleep(0.045)
            keyboard.play(0x80, pitch, 0)
            time.sleep(0.025)
        done = until(
            api,
            prefix,
            lambda s: any(
                p["player_response"] and p["player_response"]["phrase"]["activity"] == "busy"
                for p in s["plans"]
            ),
        )
        responding = next(
            p
            for p in done["plans"]
            if p["player_response"] and p["player_response"]["phrase"]["activity"] == "busy"
        )
        assert responding["player_response"]["phrase"]["register"] == "high"
        assert all(responding["player_response"][part] for part in ("drums", "bass", "piano"))
        active = until(api, prefix, lambda s: s["bar"] >= responding["bar"])
        keyboard.play(0x90, 84, 90)
        until(api, prefix, lambda s: s["midi"]["sounding"] == [84])
        keyboard.report("waiting")
        keyboard.play(0x90, 97, 100)
        disconnected = until(api, prefix, lambda s: s["midi"]["connection"] == "waiting")
        assert disconnected["midi"]["pressed"] == disconnected["midi"]["sounding"] == []
        moving = until(api, prefix, lambda s: s["bar"] > active["bar"])
        assert moving["transport"] == "playing"
        keyboard.report("connected")
        until(api, prefix, lambda s: s["midi"]["connection"] == "connected")
        keyboard.play(0x90, 62, 80)
        until(api, prefix, lambda s: s["midi"]["sounding"] == [62])
        api.patch(f"{prefix}/midi", json={"port": None})
        assert api.get(f"{prefix}/state").json()["transport"] == "playing"


def test_late_keyboard_update_preserves_last_valid_draft():
    clock = Clock()
    planner = Planner()
    scheduler = Scheduler(planner, threaded=False, clock=clock)
    try:
        fill(scheduler, planner)
        before = next(p for p in scheduler.snapshot()["plans"] if p["bar"] == 1)
        scheduler.play()
        scheduler.update_player_phrase(PlayerPhrase(register="high", activity="busy"))
        assert any(bar == 1 for bar, _, _ in scheduler.planning_targets())
        while scheduler.snapshot()["current_bar"] < 1:
            clock.step(scheduler)
        after = next(p for p in scheduler.snapshot()["plans"] if p["bar"] == 1)
        assert after == before and not after["fallback_tracks"]
    finally:
        scheduler.close()


def test_attacks_keep_their_arrival_time_when_the_event_loop_is_busy():
    """A stalled loop delivers queued key presses together; their beats must not merge."""
    from band.live_midi import LiveMidi

    async def scenario():
        planner = Planner()
        scheduler = Scheduler(planner, threaded=False)
        midi = LiveMidi(scheduler, Keyboard)
        try:
            midi.start()
            midi.configure({"port": DEVICE["id"]})
            await asyncio.sleep(0)
            assert midi.status["connection"] == "connected"
            for bar, settings, version in scheduler.planning_targets():
                scheduler.commit(await planner.plan(bar, settings, version))
            scheduler.play()
            pressed = time.perf_counter_ns()
            spacing = 140_000_000  # 140 ms apart: one sixteenth at 120 BPM is 125 ms.
            # The loop was busy while these arrived; they are handled only now, together.
            time.sleep(0.75)  # noqa: ASYNC251 - deliberately block the loop
            for index, pitch in enumerate((75, 77, 79, 81, 83, 82)):
                midi._message(DEVICE["id"], (0x90, pitch, 100), pressed + index * spacing)
            ticks = sorted({attack.tick for attack in midi.attacks})
            assert len(ticks) == 6
            midi.update_phrase()
            phrase, _ = scheduler.player_context()
            assert phrase is not None and phrase.activity == "busy"
        finally:
            await midi.close()
            scheduler.close()

    asyncio.run(scenario())
