import asyncio
import json

import httpx2

from band.conductor import Conductor
from band.music import ConductorPlan
from band.style_direction import STYLE_PROMPT_VERSION


def test_offline_style_reference_and_explicit_degree_progression():
    async def exercise():
        engine = Conductor()
        initial = ConductorPlan()
        rock = await engine.propose("摇滚", initial)
        assert rock.source == "rules" and rock.reason == "not_configured"
        assert rock.plan.profile.genre == "rock" and rock.plan.profile.groove == "straight_eighth"
        assert rock.plan.bass == "root" and rock.progression == ("C", "C", "Bb", "F") * 2

        disco = await engine.propose("像 Daft Punk 的风格", rock.plan)
        assert disco.plan.profile.reference == "Daft Punk"
        assert disco.plan.profile.basis == "reference"
        assert disco.plan.profile.groove == "four_on_floor"
        assert disco.progression == ("Cm7", "F7") * 4

        changed = await engine.propose("G大调，用I–V–vi–IV", disco.plan)
        assert changed.plan.profile.groove == disco.plan.profile.groove
        assert changed.plan.profile.basis == "theory"
        assert changed.progression == ("G", "D", "Em", "C") * 2

        lower = await engine.propose("轻一点", changed.plan)
        assert (
            lower.plan.profile == changed.plan.profile
            and lower.plan.intensity < changed.plan.intensity
        )
        assert lower.progression is None

        back_to_swing = await engine.propose("换成爵士摇摆", lower.plan)
        assert back_to_swing.plan.profile is None and back_to_swing.progression is None

    asyncio.run(exercise())


def test_harmony_can_be_kept_and_unrecognized_reference_keeps_current_profile():
    async def exercise():
        engine = Conductor()
        rock = await engine.propose("硬摇滚", ConductorPlan())
        assert rock.plan.profile.genre == "hard_rock"
        groove = await engine.propose("鼓改半速律动", rock.plan)
        assert groove.plan.profile.groove == "half_time"
        assert (
            groove.plan.profile.harmony == rock.plan.profile.harmony and groove.progression is None
        )
        reggae = await engine.propose("像 Bob Marley，保留我的和弦", groove.plan)
        assert reggae.plan.profile.groove == "one_drop" and reggae.progression is None
        assert reggae.plan.profile.harmony == "keep"
        unknown = await engine.propose("像 Example Band 一样的风格", reggae.plan)
        assert unknown.plan == reggae.plan and unknown.progression is None

    asyncio.run(exercise())


def test_model_interprets_additional_reference_and_invalid_choices_fall_back(monkeypatch):
    payloads = []
    choices = [
        {"genre": "rock", "groove": "straight_eighth", "mode": "major", "harmony": "major_145"},
        {"genre": "rock", "groove": "invalid", "mode": "major", "harmony": "major_145"},
        {"genre": "fake", "groove": "straight_eighth", "mode": "major", "harmony": "major_145"},
    ]

    def response(request):
        payloads.append(json.loads(request.content))
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": json.dumps(choices.pop(0))}}]}
        )

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx2.MockTransport(response), **kwargs),
    )
    conductor = Conductor("http://127.0.0.1:10109/v1", "test-model")
    inferred = asyncio.run(conductor.propose("像 Fleetwood Mac", ConductorPlan()))
    assert inferred.source == "llm" and inferred.prompt_version == STYLE_PROMPT_VERSION
    assert (
        inferred.plan.profile.reference == "Fleetwood Mac"
        and inferred.plan.profile.basis == "inferred"
    )
    assert inferred.progression == ("C", "C", "F", "C", "G", "F", "C", "G")
    schema = payloads[0]["response_format"]["json_schema"]["schema"]
    assert not schema["additionalProperties"] and set(schema["required"]) == {
        "genre",
        "groove",
        "mode",
        "harmony",
    }
    offline = asyncio.run(conductor.propose("蓝调", ConductorPlan()))
    assert offline.source == "rules" and offline.reason == "invalid_response"
    assert offline.plan.profile.groove == "shuffle" and offline.plan.profile.harmony == "blues_8"
