import asyncio
import json

import httpx2
import pytest

from band.conductor import Conductor
from band.music import ConductorPlan, MusicSettings
from band.style_direction import STYLE_PROMPT_VERSION


@pytest.mark.parametrize(
    ("instruction", "genre", "groove"),
    [
        ("星际穿越曲风", "cinematic", "straight_eighth"),
        ("氛围音乐", "ambient", "straight_eighth"),
        ("电子舞曲", "electronic", "four_on_floor"),
        ("萨尔萨", "latin", "bossa"),
        ("乡村乐", "country", "shuffle"),
        ("嘻哈", "hip_hop", "half_time"),
        ("巴洛克", "classical", "straight_eighth"),
    ],
)
def test_added_families_have_offline_arrangements(instruction, genre, groove):
    result = asyncio.run(Conductor().propose(instruction, ConductorPlan()))
    assert result.source == "rules" and result.prompt_version == STYLE_PROMPT_VERSION
    assert (result.plan.profile.genre, result.plan.profile.groove) == (genre, groove)
    assert result.progression is not None and len(result.progression) == 8


def test_free_text_reaches_model_and_uses_the_returned_traits(monkeypatch):
    requests = []
    responses = iter(
        [
            {"genre": "ambient", "groove": "half_time", "mode": "dorian", "harmony": "dorian_vamp"},
            {"genre": "keep", "groove": "shuffle", "mode": "keep", "harmony": "keep"},
            {"genre": "keep", "groove": "keep", "mode": "keep", "harmony": "keep"},
        ]
    )

    def complete(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": json.dumps(next(responses))}}]}
        )

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx2.MockTransport(complete), **kwargs),
    )
    conductor = Conductor("http://127.0.0.1:10109/v1", "test-model")

    async def exercise():
        imagined = await conductor.propose(
            "蛾群沿着路灯忽远忽近，在暗处留出呼吸感", ConductorPlan()
        )
        assert imagined.source == "llm" and imagined.plan.profile.basis == "inferred"
        assert imagined.plan.profile.genre == "ambient"
        assert imagined.plan.profile.groove == "half_time"
        assert imagined.progression == ("Cm7", "F7") * 4
        rhythm = await conductor.propose("像钟摆来回晃，但不要改和弦", imagined.plan)
        assert rhythm.plan.profile.genre == "ambient"
        assert rhythm.plan.profile.groove == "shuffle"
        assert rhythm.progression is None
        unknown = await conductor.propose("浮空电鳗四重奏曲风", rhythm.plan)
        assert unknown.plan == rhythm.plan
        assert unknown.reason == "unrecognized_reference"

    asyncio.run(exercise())
    assert all(
        request["response_format"]["json_schema"]["name"] == "style_direction"
        for request in requests
    )


def test_explicit_rhythm_blend_keeps_the_lead_style_and_harmony():
    result = asyncio.run(Conductor().propose("把摇滚和雷鬼律动混一下，不要改和弦", ConductorPlan()))
    assert result.plan.profile.genre == "rock"
    assert result.plan.profile.groove == "one_drop"
    assert result.progression is None


def test_literal_score_request_does_not_guess_replacement_music():
    engine = Conductor()
    current = MusicSettings()
    result = asyncio.run(
        engine.propose_with_timing("原封不动演奏《星际穿越》的原曲，120 BPM", current)
    )
    assert result.reason == "score_required"
    assert result.plan == current.conductor and result.progression is None
    assert result.bpm is None and result.meter is None
