import json

from fastapi.testclient import TestClient
from test_live_midi import DEVICE, Keyboard, until

from band.api import create_app
from band.planner import Planner
from band.timing import Scheduler


class Synth:
    def __init__(self):
        self.ready = True
        self.events = []
        self.silences = 0
        self.closed = False

    def poll(self):
        return self.ready

    def deliver(self, event):
        self.events.append(event)

    def silence_all(self):
        self.silences += 1

    def close(self):
        self.closed = True


def test_source_handoff_replays_only_held_sustained_voices_and_stamps_events():
    received = []
    scheduler = Scheduler(Planner(), threaded=False, output=received.append)
    try:
        scheduler.switch_audio("supercollider", None)
        scheduler.record_midi("midi_on", event_id="keys-a", pitch=72, velocity=80, volume=0.8)
        scheduler.record_midi("midi_on", event_id="keys-b", pitch=74, velocity=81, volume=0.8)
        scheduler.record_midi("midi_off", event_id="keys-b", pitch=74)
        scheduler.record_midi("midi_level", volume=0.5)
        scheduler.record_midi("note_on", track_id="drums", event_id="kick", pitch=36)
        scheduler.record_midi("note_on", track_id="bass", event_id="bass", pitch=36)
        scheduler.switch_audio("browser", "disconnected")
        scene = received[-1]
        assert scene["type"] == "audio_route"
        assert scene["audio_output"] == "browser"
        assert [(item["event_id"], item.get("volume")) for item in scene["resume"]] == [
            ("keys-a", 0.5),
            ("bass", None),
        ]
        assert all(held["audio_output"] == "browser" for held in scene["resume"])
        assert received[1]["audio_output"] == "supercollider"
        assert scheduler.snapshot()["audio_active"] == scene["resume"]
        scheduler.record_midi("silence")
        assert [note["event_id"] for note in scheduler.snapshot()["audio_active"]] == ["keys-a"]
        scheduler.record_midi("midi_silence")
        assert not scheduler.snapshot()["audio_active"]
    finally:
        scheduler.close()


def test_supercollider_failover_and_recovery_preserve_transport_and_held_keyboard(tmp_path):
    native, sc = Synth(), Synth()
    midi = []

    def make_input(message, status):
        midi.append(Keyboard(message, status))
        return midi[-1]

    app = create_app(
        tmp_path,
        audio_factory=lambda: native,
        sc_factory=lambda: sc,
        midi_factory=make_input,
    )
    with TestClient(app, base_url="http://localhost") as api:
        session = api.post("/sessions", json={"bpm": 240}).json()
        prefix = f"/sessions/{session['id']}"
        until(api, prefix, lambda s: s["audio_output"] == "supercollider")
        api.patch(f"{prefix}/midi", json={"port": DEVICE["id"]})
        until(api, prefix, lambda s: s["midi"]["connection"] == "connected")
        api.post(f"{prefix}/play")
        midi[0].play(0x90, 80, 82)
        flowing = until(
            api,
            prefix,
            lambda s: (
                s["midi"]["sounding"] == [80]
                and {event.get("track_id") for event in sc.events if event["type"] == "note_on"}
                == {"drums", "bass", "piano"}
            ),
        )
        assert not any(event["type"] == "midi_on" for event in native.events)
        first_silences = native.silences
        sc.ready = False
        failover = until(api, prefix, lambda s: s["audio_output"] == "native")
        assert failover["transport"] == "playing"
        assert failover["audio_preferred"] == "supercollider"
        assert failover["audio_reason"] == "disconnected"
        until(
            api,
            prefix,
            lambda s: (
                s["bar"] > flowing["bar"]
                and any(e["type"] == "midi_on" and e["pitch"] == 80 for e in native.events)
            ),
        )
        sc.ready = True
        recovered = until(api, prefix, lambda s: s["audio_output"] == "supercollider")
        assert recovered["transport"] == "playing"
        until(
            api,
            prefix,
            lambda _: (
                native.silences == first_silences + 1
                and sum(e["type"] == "midi_on" and e["pitch"] == 80 for e in sc.events) == 2
            ),
        )
        midi[0].play(0x80, 80, 0)
        until(api, prefix, lambda s: not s["midi"]["sounding"])
        api.post(f"{prefix}/stop")
    assert sc.closed and native.closed
    records = [
        json.loads(line)
        for line in (tmp_path / "sessions" / f"{session['id']}.jsonl").read_text().splitlines()
    ]
    assert [e["audio_output"] for e in records if e["type"] == "audio_route"] == [
        "supercollider",
        "native",
        "supercollider",
    ]
    assert [e["transport"] for e in records if e["type"] == "transport"][:2] == [
        "playing",
        "stopped",
    ]


def test_requested_supercollider_can_start_without_the_optional_install(tmp_path):
    with TestClient(create_app(tmp_path, prefer_sc=True), base_url="http://localhost") as api:
        state = api.post("/sessions").json()
        assert state["audio_output"] == state["audio_fallback"] == "browser"
        assert state["audio_preferred"] == "supercollider"
        assert state["audio_reason"] == "unavailable"
