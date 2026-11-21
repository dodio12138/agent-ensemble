import asyncio
from io import BytesIO

import pytest
from mido import MidiFile
from pydantic import ValidationError

from band.midi import export_midi
from band.music import (
    BAR_TICKS,
    PPQ,
    TRACK_DEFINITIONS,
    TRACKS,
    BarPlan,
    MusicSettings,
    Note,
    TrackSettings,
    active_tracks,
)
from band.planner import AgentOutput, BassAgent, Planner
from band.timing import Meter, Scheduler, TempoMap


class Clock:
    now = 1_000_000_000

    def __call__(self):
        return self.now

    def step(self, scheduler):
        self.now = scheduler.next_deadline_ns()
        return scheduler.pump()


def fill(scheduler, planner):
    async def commit():
        for bar, settings, version in scheduler.planning_targets():
            assert scheduler.commit(await planner.plan(bar, settings, version))

    asyncio.run(commit())


def test_time_conversion_uses_integer_ticks_and_continuous_tempo():
    meter = Meter(7, 8)
    assert meter.from_tick(meter.to_tick(21, 6, 479)) == (21, 6, 479)
    tempo = TempoMap(((0, 120), (BAR_TICKS, 80), (BAR_TICKS * 2, 137)))
    assert tempo.tick_to_ns(BAR_TICKS + PPQ) == 2_750_000_000
    for tick in range(0, BAR_TICKS * 5, 13):
        due = tempo.tick_to_ns(tick)
        assert tempo.ns_to_tick(due) == tick
        if due:
            assert tempo.ns_to_tick(due - 1) == tick - 1


def test_validation_rejects_cross_bar_channel_duplicate_and_overlap():
    args = {
        "event_id": "piano-0-test",
        "track_id": "piano",
        "bar": 0,
        "start_tick": 300,
        "duration_ticks": 100,
        "channel": TRACKS["piano"].channel,
        "pitch": 60,
        "velocity": 80,
    }
    valid = Note(**args)
    for mutation in ({"duration_ticks": BAR_TICKS}, {"channel": 9}, {"pitch": 10}, {"velocity": 0}):
        with pytest.raises(ValidationError):
            Note(**{**args, **mutation})
    for other in (valid, Note(**{**args, "event_id": "different", "start_tick": 375})):
        with pytest.raises(ValidationError):
            BarPlan(bar=0, version=0, notes=(valid, other))


class DelayedBass(BassAgent):
    async def generate(self, context):
        await asyncio.sleep(2)


class FailedBass(BassAgent):
    async def generate(self, context):
        raise RuntimeError("injected failure")


class RepeatedBass(BassAgent):
    async def generate(self, context):
        output = await super().generate(context)
        return AgentOutput(output.intent, (*output.notes, output.notes[0]))


@pytest.mark.parametrize(
    ("bass", "status"),
    [(DelayedBass(), "timeout"), (FailedBass(), "failed"), (RepeatedBass(), "invalid")],
)
def test_fault_is_confined_to_one_musician(bass, status):
    music = MusicSettings(progression=("F#m7b5", "B7b9", "Em9", "Cmaj7/G") * 2)
    planner = Planner(agents={"bass": bass}, deadline=0.01)
    plan = asyncio.run(planner.plan(3, music, 2))
    assert plan.fallback_tracks == ("bass",)
    assert tuple(decision.status for decision in plan.intents) == (
        "ready",
        status,
        "ready",
        "ready",
    )
    assert {note.track_id for note in plan.notes} - {"vocal"} == TRACKS.keys()
    first_bass = next(note for note in plan.notes if note.track_id == "bass")
    assert first_bass.pitch % 12 == 7


def test_seed_reproduces_plans_and_arrangements_use_three_tracks():
    async def arrange(seed):
        planner = Planner()
        settings = MusicSettings(seed=seed)
        tracks = {
            key: TrackSettings(program=track.program) for key, track in TRACK_DEFINITIONS.items()
        }
        return export_midi(
            settings,
            tracks,
            [await planner.plan(bar, settings, 0) for bar in range(8)],
        )

    data = asyncio.run(arrange(42))
    assert data == asyncio.run(arrange(42))
    assert data != asyncio.run(arrange(52))
    midi = MidiFile(file=BytesIO(data))
    assert midi.type == 1
    assert midi.ticks_per_beat == PPQ
    assert [track.name for track in midi.tracks] == ["Conductor", "Drums", "Bass", "Piano", "Voice"]
    trio = [(track, TRACK_DEFINITIONS[track]) for track in ("drums", "bass", "piano", "vocal")]
    for (track_id, definition), track in zip(trio, midi.tracks[1:], strict=True):
        active = set()
        for message in track:
            if message.type == "note_on":
                assert message.channel == definition.channel
                assert message.note not in active, track_id
                active.add(message.note)
            if message.type == "note_off":
                active.remove(message.note)
        assert not active
        assert sum(message.time for message in track) == 8 * BAR_TICKS


def test_clock_locks_bar_and_applies_staged_settings_without_losing_notes():
    clock = Clock()
    planner = Planner()
    scheduler = Scheduler(planner, clock=clock, threaded=False)
    try:
        fill(scheduler, planner)
        scheduler.play()
        scheduler.pump()
        initial = scheduler.events_after(0)
        start = next(item for item in initial if item["type"] == "bar")["due_ns"]
        assert scheduler.snapshot()["transport"] == "playing"
        while scheduler.snapshot()["locked_until_bar"] < 1:
            clock.step(scheduler)
        state = scheduler.stage_settings({"bpm": 180, "progression": ["F7"] * 8})
        assert state["settings"]["bpm"] == 120
        assert state["pending"]["pickup_bar"] == 2
        assert state["pending"]["bar"] == 3
        scheduler.stage_track("bass", {"muted": True})
        while scheduler.snapshot()["current_bar"] < 1:
            clock.step(scheduler)
        stale = asyncio.run(planner.plan(3, MusicSettings(), 0))
        assert not scheduler.commit(stale)
        fill(scheduler, planner)
        while scheduler.snapshot()["current_bar"] < 3:
            clock.step(scheduler)
        active = scheduler.snapshot()
        assert active["settings"]["bpm"] == 180 and active["settings"]["progression"] == ["F7"] * 8
        assert active["tracks"]["bass"]["muted"] and active["pending"] is None
        before = active["sequence"]
        assert scheduler.pause()["transport"] == "paused"
        assert scheduler.play()["current_bar"] == 3
        clock.step(scheduler)
        assert scheduler.stop()["current_bar"] == 0
        events = scheduler.events_after(0)
        notes = set()
        for event in events:
            if event["type"] == "note_on":
                assert event["event_id"] not in notes
                notes.add(event["event_id"])
            if event["type"] == "note_off":
                notes.remove(event["event_id"])
        assert not notes
        assert not any(
            event["type"] == "note_on" and event["bar"] == 3 and event["track_id"] == "bass"
            for event in events
        )
        assert any(event["type"] == "silence" for event in scheduler.events_after(before))
        assert [event["due_ns"] - start for event in events if event["type"] == "bar"][:4] == [
            0,
            2_000_000_000,
            4_000_000_000,
            6_000_000_000,
        ]
    finally:
        scheduler.close()


def test_scheduler_survives_total_planning_outage_and_disabling_loop():
    clock = Clock()
    scheduler = Scheduler(
        Planner(),
        MusicSettings(loop=False),
        clock=clock,
        threaded=False,
    )
    try:
        scheduler.play()
        while scheduler.snapshot()["transport"] == "playing":
            clock.step(scheduler)
        events = scheduler.events_after(0)
        bars = [event for event in events if event["type"] == "bar"]
        assert [event["bar"] for event in bars] == list(range(8))
        plans = [
            event for event in events if event["type"] == "plan" and event["status"] == "locked"
        ]
        assert len(plans) == 8
        assert all(set(event["fallback_tracks"]) == set(active_tracks("trio")) for event in plans)
        assert any(event.get("reason") == "ended" for event in events)
    finally:
        scheduler.close()
