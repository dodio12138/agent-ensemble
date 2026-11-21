import asyncio
import json
import time

import httpx2
from fastapi.testclient import TestClient

from band.api import create_app
from band.conductor import Conductor
from band.music import MusicSettings
from band.style_arrangement import ArrangementChoice, materialize

HARD_ROCK = {
    "groove": "straight_eighth",
    "harmony": "minor_1767",
    "mode": "minor",
    "tonic": "E",
    "bpm": 150,
    "intensity": 88,
    "density": 0.8,
    "drums": "driving",
    "bass": "root",
    "keys": "sustained",
    "rhythm_guitar": "distorted",
    "lead_guitar": "distorted",
    "keys_tone": "organ",
    "pad": "strings",
    "note": "失真强力和弦配八分根音，管风琴铺底",
}


def fake_model(monkeypatch, choice, payloads=None):
    def response(request):
        if payloads is not None:
            payloads.append(json.loads(request.content))
        body = {"model": "served", "choices": [{"message": {"content": json.dumps(choice)}}]}
        return httpx2.Response(200, json=body)

    original = httpx2.AsyncClient

    def client_factory(**kwargs):
        return original(transport=httpx2.MockTransport(response), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", client_factory)


def test_rules_correct_out_of_range_choices_and_map_tones():
    wild = ArrangementChoice.model_validate(
        {**HARD_ROCK, "groove": "bossa", "bpm": 230, "harmony": "keep", "intensity": 3}
    )
    settings = MusicSettings(lineup="modern", progression=("Am", "G", "F", "G") * 2)
    arranged = materialize("hard_rock", wild, settings)
    assert arranged.plan.profile.genre == "hard_rock"
    assert arranged.plan.profile.groove == "straight_eighth"  # bossa is not a rock groove
    assert arranged.bpm == 170 and arranged.plan.intensity == 70  # hard rock is never 3%
    assert arranged.progression is None  # "keep" leaves the current chords alone
    assert len(arranged.corrections) == 3
    assert arranged.tracks["rhythm_guitar"]["program"] == 30
    assert arranged.tracks["piano"]["program"] == 19
    assert arranged.tracks["synth"]["program"] == 48


def test_model_arranges_a_style_with_harmony_in_its_key(monkeypatch):
    payloads = []
    fake_model(monkeypatch, HARD_ROCK, payloads)
    engine = Conductor("http://127.0.0.1:10109/v1", "test-model", timeout=2)
    arranged, attempt = asyncio.run(engine.arrange_genre("hard_rock", MusicSettings()))
    assert attempt.model == "served" and arranged is not None
    assert arranged.progression[0] == "Em" and arranged.bpm == 150
    assert arranged.plan.piano == "sustained" and arranged.plan.intensity == 88
    request = payloads[0]
    context = json.loads(request["messages"][1]["content"])
    assert context["style"] == "hard_rock" and context["bpm_range"] == [110, 170]
    schema = request["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == HARD_ROCK.keys()


def test_style_click_applies_rules_now_and_the_model_arrangement_next(tmp_path, monkeypatch):
    fake_model(monkeypatch, HARD_ROCK)
    conductor = Conductor("http://127.0.0.1:10109/v1", "test-model", timeout=2)
    app = create_app(tmp_path, conductor=conductor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        sid = client.post("/sessions", json={"lineup": "modern"}).json()["id"]
        first = client.post(f"/sessions/{sid}/preset", json={"style": "hard_rock"}).json()
        assert first["conductor"]["source"] == "preset"
        state = first
        for _ in range(100):
            state = client.get(f"/sessions/{sid}/state").json()
            if (state["refinement"] or {}).get("status") == "applied":
                break
            time.sleep(0.02)
        refinement = state["refinement"]
        assert refinement["status"] == "applied" and refinement["note"] == HARD_ROCK["note"]
        assert state["conductor"]["source"] == "llm"
        assert state["conductor"]["note"] == HARD_ROCK["note"]
        assert state["settings"]["bpm"] == 150 and state["settings"]["progression"][0] == "Em"
        assert state["tracks"]["rhythm_guitar"]["program"] == 30
        assert state["settings"]["conductor"]["piano"] == "sustained"


def test_without_a_model_the_rule_preset_stays(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        sid = client.post("/sessions").json()["id"]
        state = client.post(f"/sessions/{sid}/preset", json={"style": "disco"}).json()
        assert state["refinement"] == {
            "genre": "disco",
            "status": "skipped",
            "reason": "not_configured",
        }
        assert state["settings"]["conductor"]["profile"]["groove"] == "four_on_floor"


def test_rules_keep_style_typical_tones_and_explain_when_the_model_does_not():
    weak = ArrangementChoice.model_validate(
        {**HARD_ROCK, "lead_guitar": "clean", "rhythm_guitar": "acoustic", "note": "hard rock"}
    )
    arranged = materialize("hard_rock", weak, MusicSettings(lineup="modern"))
    assert arranged.tracks["rhythm_guitar"]["program"] == 29  # overdriven
    assert arranged.tracks["lead_guitar"]["program"] == 30  # distorted
    assert arranged.note.startswith("平直八分") and "强度 88%" in arranged.note
    gentle = ArrangementChoice.model_validate({**HARD_ROCK, "note": "hard rock"})
    ballad = materialize("ballad", gentle, MusicSettings())
    assert ballad.plan.intensity == 50 and ballad.density == 0.5
    assert ballad.tracks["lead_guitar"]["program"] == 27  # clean
