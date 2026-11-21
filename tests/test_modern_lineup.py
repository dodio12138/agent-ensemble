from io import BytesIO

from fastapi.testclient import TestClient
from mido import MidiFile
from test_engine import Clock

from band.api import create_app
from band.music import MusicSettings, active_tracks
from band.planner import Planner
from band.timing import Scheduler


def test_lineup_switches_at_bar_boundary_with_active_transport():
    clock = Clock()
    scheduler = Scheduler(Planner(), clock=clock, threaded=False)
    try:
        scheduler.play()
        staged = scheduler.stage_settings({"lineup": "modern"})
        bar = staged["pending"]["bar"]
        assert tuple(staged["tracks"]) == active_tracks("trio")
        assert tuple(staged["pending"]["tracks"]) == active_tracks("modern")
        assert staged["pending"]["tracks"]["bass"]["program"] == 33
        state = staged
        while state["current_bar"] < bar + 1:
            state = clock.step(scheduler)
        assert tuple(state["tracks"]) == active_tracks("modern")
        assert state["transport"] == "playing"
        staged = scheduler.stage_settings({"lineup": "trio"})
        bar = staged["pending"]["bar"]
        while state["current_bar"] < bar + 1:
            state = clock.step(scheduler)
        assert tuple(state["tracks"]) == active_tracks("trio")
        assert state["transport"] == "playing"
        assert state["tracks"]["bass"]["program"] == 32
        events = scheduler.events_after(0)
        extra = set(active_tracks("modern")) - set(active_tracks("trio"))
        assert {event["track_id"] for event in events if event.get("reason") == "lineup"} == extra
        assert not any(
            event["type"] == "note_on" and event["track_id"] in extra and event["bar"] >= bar
            for event in events
        )
    finally:
        scheduler.close()


def test_six_midi_tracks_and_mixer_state_survive_lineup_toggles(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        payload = MusicSettings(lineup="modern").model_dump(mode="json")
        created = client.post("/sessions", json=payload).json()
        prefix = f"/sessions/{created['id']}"
        assert created["tracks"]["piano"]["program"] == 4
        response = client.get(f"{prefix}/export.mid")
        response.raise_for_status()
        midi = MidiFile(file=BytesIO(response.content))
        assert [track.name for track in midi.tracks] == [
            "Conductor",
            "Drums",
            "Bass",
            "Keys",
            "Rhythm Guitar",
            "Lead Guitar",
            "Synth Pad",
        ]
        for track, channel in zip(midi.tracks[1:], (9, 1, 0, 3, 4, 5), strict=True):
            notes = [event for event in track if event.type == "note_on"]
            assert notes and {event.channel for event in notes} == {channel}
        assert (
            client.patch(f"{prefix}/tracks/lead_guitar", json={"volume": 0.38}).status_code == 200
        )
        assert client.patch(f"{prefix}/tracks/bass", json={"program": 33}).status_code == 200
        trio = client.patch(f"{prefix}/settings", json={"lineup": "trio"}).json()
        assert trio["tracks"]["bass"]["program"] == 33
        assert trio["tracks"]["piano"]["program"] == 0
        modern = client.patch(f"{prefix}/settings", json={"lineup": "modern"}).json()
        assert modern["tracks"]["lead_guitar"]["volume"] == 0.38
