from io import BytesIO

from fastapi.testclient import TestClient
from mido import MidiFile

from band.api import create_app


def test_console_workflow_websocket_midi_and_audit(tmp_path):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1")
    with client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/sessions/missing/state").status_code == 404
        assert client.post("/sessions", headers={"origin": "https://other.example"}).status_code == 403
        response = client.post("/sessions")
        assert response.status_code == 201
        state = response.json()
        prefix = f"/sessions/{state['id']}"
        assert state["transport"] == "stopped"
        assert [plan["bar"] for plan in state["plans"]] == [0, 1, 2]
        assert client.patch(f"{prefix}/settings", json={"bpm": 500}).status_code == 422
        assert client.patch(f"{prefix}/settings", json={"progression": ["invalid"] * 8}).status_code == 422
        assert client.patch(f"{prefix}/tracks/bass", json={"volume": None}).status_code == 422

        state = client.patch(f"{prefix}/settings", json={"bpm": 240, "loop": False}).json()
        assert state["settings"]["bpm"] == 240
        with client.websocket_connect(f"ws://127.0.0.1{prefix}/events") as websocket:
            initial = websocket.receive_json()
            assert initial["events"] == []
            assert initial["state"]["sequence"] == state["sequence"]
            assert client.post(f"{prefix}/play").json()["transport"] == "playing"
            records = []
            for _ in range(20):
                records.extend(websocket.receive_json()["events"])
                if any(event["type"] == "note_on" for event in records):
                    break
            assert any(event["type"] == "note_on" for event in records)
            assert [event["sequence"] for event in records] == sorted({
                event["sequence"] for event in records
            })
            pending = client.patch(f"{prefix}/tracks/drums", json={"muted": True}).json()
            assert pending["pending"]["bar"] > pending["bar"]
            assert client.post(f"{prefix}/pause").json()["transport"] == "paused"
            assert client.post(f"{prefix}/stop").json()["tracks"]["drums"]["muted"]

        midi_response = client.get(f"{prefix}/export.mid")
        assert midi_response.status_code == 200
        midi = MidiFile(file=BytesIO(midi_response.content))
        assert len(midi.tracks) == 5  # conductor, drums, bass, piano, voice
        assert not any(message.type == "note_on" for message in midi.tracks[1])
    data = (tmp_path / "sessions" / f"{state['id']}.jsonl").read_text()
    assert '"type":"note_on"' in data
    assert '"type":"note_off"' in data
    assert '"type":"silence"' in data
