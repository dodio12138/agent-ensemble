import asyncio
import json

import httpx2
import pytest

from band.conductor import Conductor, style_preset
from band.improvisation import IMPROVISATION_PROMPT_VERSION, improvisation_choices
from band.music import MusicSettings, parse_chord


@pytest.mark.parametrize("style", ("swing", "bossa", "funk", "ballad"))
def test_style_preset_has_matching_style_and_arrangement(style):
    preset = style_preset(style)
    assert preset.style == style and 1 <= preset.intensity <= 100
    if style == "funk":
        assert preset.drums == "driving" and preset.bass == "syncopated"
    if style == "ballad":
        assert preset.piano == "sustained" and preset.drums == "sparse"


def test_rule_improvisation_varies_harmony_and_energy_and_obeys_keep_request():
    async def exercise():
        conductor = Conductor()
        settings = MusicSettings(
            progression=("Cmaj7/G", "Ebm7", "Ab7", "Dbmaj7", "Bdim7", "F#m7b5", "A7", "D7")
        )
        later = set()
        for variant in range(12):
            result = await conductor.improvise("先让钢琴领奏，轻柔一点", settings, variant)
            assert result.source == "rules"
            assert result.reason == "not_configured"
            assert result.plan.progression[0] == settings.progression[0]
            assert result.plan.phrases[0].lead == "piano"
            assert result.plan.phrases[0].character == "space"
            assert result.plan.phrases[-1].character == "resolve"
            assert (
                sum(
                    old != new
                    for old, new in zip(settings.progression, result.plan.progression, strict=True)
                )
                == 2
            )
            assert all(parse_chord(chord).symbol == chord for chord in result.plan.progression)
            later.add(result.plan.progression)
        assert len(later) > 2

        kept = await conductor.improvise("保留原和弦，让贝斯先来", settings, 0)
        assert kept.plan.progression == settings.progression
        assert kept.plan.phrases[0].lead == "bass"

    asyncio.run(exercise())


def test_structured_model_choices_drive_harmony_and_roles(monkeypatch):
    contexts = []

    def provider(request):
        payload = json.loads(request.content)
        document = json.loads(payload["messages"][1]["content"])
        contexts.append(document)
        assert payload["response_format"]["json_schema"]["name"] == "improvisation"
        decision = {
            "a": next(iter(document["first_half"])),
            "b": next(iter(document["second_half"])),
            "order": "bass/drums/piano",
            "finish": "bass",
            "arc": "trade",
        }
        return httpx2.Response(
            200,
            json={"model": "test-rev", "choices": [{"message": {"content": json.dumps(decision)}}]},
        )

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(provider), **kwargs),
    )
    settings = MusicSettings()
    instruction = "贝斯先领奏，鼓组回应"
    result = asyncio.run(
        Conductor("http://127.0.0.1:54321/v1", "test-model").improvise(instruction, settings, 0)
    )
    assert (result.source, result.model, result.prompt_version, result.attempts, result.reason) == (
        "llm",
        "test-rev",
        IMPROVISATION_PROMPT_VERSION,
        1,
        None,
    )
    assert contexts[0]["instruction"] == instruction
    assert (result.plan.phrases[0].lead, result.plan.phrases[1].lead) == ("bass", "drums")
    assert [phase.character for phase in result.plan.phrases] == [
        "answer",
        "space",
        "answer",
        "resolve",
    ]
    choices = improvisation_choices(instruction, settings, 0)
    for half in (choices.first, choices.second):
        move = next(iter(half.values()))
        assert move and result.plan.progression[move.position] == move.chord


def test_unknown_model_choices_trigger_rule_fallback(monkeypatch):
    response = {
        "a": "99",
        "b": "99",
        "order": "bass/drums/piano",
        "finish": "bass",
        "arc": "trade",
    }
    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(
                    200, json={"choices": [{"message": {"content": json.dumps(response)}}]}
                )
            ),
            **kwargs,
        ),
    )
    settings = MusicSettings()
    result = asyncio.run(
        Conductor("http://127.0.0.1:54321/v1", "test-model").improvise("来点变化", settings, 2)
    )
    assert (result.source, result.reason, result.attempts) == ("rules", "invalid_response", 2)
    assert result.plan.progression != settings.progression


def test_clear_lead_cues_and_build_arc_are_retained_without_model():
    text = "钢琴开头留白，贝斯第二段领奏，鼓在高点加码，钢琴收尾。先轻后浓。"
    result = asyncio.run(Conductor().improvise(text, MusicSettings(), 0))
    assert [phase.lead for phase in result.plan.phrases] == ["piano", "bass", "drums", "piano"]
    assert [phase.character for phase in result.plan.phrases] == [
        "space",
        "answer",
        "build",
        "resolve",
    ]


def test_improvisation_timeout_keeps_a_playable_section():
    class SlowConductor(Conductor):
        async def _complete(self, client, **kwargs):
            await asyncio.sleep(1)
            raise AssertionError("Call should be cancelled within the request budget")

    conductor = SlowConductor("http://127.0.0.1:54321/v1", "test-model", timeout=0.08)
    result = asyncio.run(conductor.improvise("先轻后浓", MusicSettings(), 0))
    assert result.source == "rules" and result.reason == "timeout"
    assert len(result.plan.progression) == 8
    assert result.plan.phrases[-1].character == "resolve"
