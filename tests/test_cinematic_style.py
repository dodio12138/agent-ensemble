import asyncio
from io import BytesIO

from fastapi.testclient import TestClient
from mido import MidiFile

from band.api import create_app
from band.conductor import Conductor
from band.music import MusicSettings
from band.planner import Planner
from band.style_direction import style_intent


def test_interstellar_reference_becomes_an_original_cinematic_arrangement():
    async def check():
        settings = MusicSettings(lineup="modern")
        result = await Conductor().propose_with_timing("星际穿越曲风", settings)
        assert result.plan.profile.genre == "cinematic"
        assert result.plan.profile.reference == "《星际穿越》配乐"
        assert result.plan.profile.mode == "minor"
        assert result.progression == ("Cm", "Bb", "Ab", "Bb") * 2
        assert result.bpm is None and result.meter is None
        scored = settings.model_copy(
            update={"conductor": result.plan, "progression": result.progression}
        )
        plan = await Planner().plan(0, scored, 0)
        assert scored.score_keys_program(0) == 19
        assert len({note.start_tick for note in plan.notes if note.track_id == "piano"}) >= 4
        assert len([note for note in plan.notes if note.track_id == "bass"]) == 1
        assert all(intent.status == "ready" for intent in plan.intents)
        assert style_intent("《蓝色砂砾》曲风").reference == "蓝色砂砾"

    asyncio.run(check())


def test_cinematic_organ_is_reported_exported_and_can_be_overridden(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as api:
        session = api.post("/sessions", json={"lineup": "modern"}).raise_for_status().json()
        url = f"/sessions/{session['id']}"
        state = (
            api.post(url + "/conduct", json={"instruction": "星际穿越曲风"})
            .raise_for_status()
            .json()
        )
        assert state["conductor"]["status"] == "applied"
        assert state["tracks"]["piano"]["program"] == 19

        def export_program():
            midi = MidiFile(file=BytesIO(api.get(url + "/export.mid").raise_for_status().content))
            return next(event.program for event in midi.tracks[3] if event.type == "program_change")

        assert export_program() == 19
        api.patch(url + "/tracks/piano", json={"program": 4}).raise_for_status()
        assert export_program() == 4
