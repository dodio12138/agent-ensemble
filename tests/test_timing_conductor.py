import asyncio
import json

import httpx2
import pytest

from band.conductor import Conductor
from band.music import ConductorPlan, MusicSettings


@pytest.mark.parametrize(
    ("instruction", "bpm", "meter", "boundary"),
    [
        ("108 BPM，6/8 拍子", 108, "6/8", None),
        ("八分之六拍，速度调到 96", 96, "6/8", None),
        ("用三四拍华尔兹", None, "3/4", None),
        ("下一轮要两大拍，每拍分成三等份", None, "6/8", "next_chorus"),
        ("提速 12 BPM", 132, None, None),
        ("速度从 96 改到 102", 102, None, None),
        ("减慢 15 BPM", 105, None, None),
        ("别每段都变，下一轮 108BPM，6/8 拍", 108, "6/8", "next_chorus"),
        ("速度保持不变，下一段用 6/8", None, "6/8", "next_chorus"),
        ("下一个八小节，慢一点", 110, None, "next_chorus"),
        ("不要改成 6/8", None, None, None),
        ("不要把速度改成 104", None, None, None),
        ("先不要改速度，下段再说", None, None, None),
        ("15 BPM，7/8", None, None, None),
        ("主歌 100 BPM，进副歌才 6/8", None, None, None),
    ],
)
def test_timing_directions_work_offline(instruction, bpm, meter, boundary):
    current = MusicSettings()
    result = asyncio.run(Conductor().propose_with_timing(instruction, current))
    assert (result.bpm, result.meter, result.timing_boundary) == (bpm, meter, boundary)
    assert result.source == "rules"
    assert result.plan == current.conductor


def test_timing_keeps_scope_and_can_combine_with_a_new_style():
    async def check():
        current = MusicSettings(bpm=112, meter="6/8")
        engine = Conductor()
        ordinary = await engine.propose_with_timing("更热烈一点，钢琴用短音", current)
        assert ordinary.bpm is None and ordinary.meter is None
        assert ordinary.plan.intensity > current.conductor.intensity
        assert ordinary.plan.piano == "syncopated"
        feel = await engine.propose_with_timing("鼓改半速律动", current)
        assert feel.bpm is None and feel.meter is None
        assert feel.plan.profile.groove == "half_time"
        combined = await engine.propose_with_timing("80 BPM 的放克，四四拍，强度 44%", current)
        assert (combined.bpm, combined.meter) == (80, "4/4")
        assert combined.plan.style == "funk" and combined.plan.intensity == 44

    asyncio.run(check())


def test_permitted_model_meter_and_fixed_bpm_use_the_same_proposal(monkeypatch):
    payloads = []
    http_client = httpx2.AsyncClient

    def provider(request):
        payloads.append(json.loads(request.content))
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": '{"bpm":180,"meter":"6/8"}'}}]}
        )

    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: http_client(transport=httpx2.MockTransport(provider), **kwargs),
    )
    engine = Conductor("http://127.0.0.1:8009/v1", "test-model", timeout=1)
    result = asyncio.run(
        engine.propose_with_timing(
            "下一轮 108 BPM，拍号你来决定是否需要调整成两大拍",
            MusicSettings(conductor=ConductorPlan(intensity=30)),
        )
    )
    assert (result.bpm, result.meter, result.timing_boundary) == (108, "6/8", "next_chorus")
    assert result.plan.intensity == 30 and result.source == "llm"
    assert (
        len(payloads) == 1
        and "timing_direction" == payloads[0]["response_format"]["json_schema"]["name"]
    )
    assert json.loads(payloads[0]["messages"][-1]["content"])["choose"] == ["meter"]


def test_automatic_timing_requires_a_reason_and_copes_with_model_failure():
    engine = Conductor()
    current = MusicSettings(bpm=120, meter="3/4")
    held = asyncio.run(engine.propose_with_timing("指挥自行决定是否改速度和拍号", current))
    assert held.bpm is None and held.meter is None
    change = asyncio.run(
        engine.propose_with_timing("下一段留出呼吸空间，速度和拍号按需调整", current)
    )
    assert (change.bpm, change.meter, change.timing_boundary) == (110, None, "next_chorus")


def test_general_permission_does_not_invoke_the_timing_model():
    class NoTimingProvider(Conductor):
        async def _complete(self, client, **kwargs):
            pytest.fail("The instruction gives no musical reason to change timing")

    engine = NoTimingProvider("http://127.0.0.1:8009/v1", "test-model", timeout=1)
    result = asyncio.run(
        engine.propose_with_timing("指挥自行决定是否改速度和拍号", MusicSettings())
    )
    assert result.attempts == 0 and result.bpm is None and result.meter is None
