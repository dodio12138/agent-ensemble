import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from band.api import create_app
from band.conductor import PROMPT_VERSION, Conductor, rule_plan
from band.music import PPQ, TRACKS, ConductorPlan, MusicSettings, active_tracks
from band.planner import Planner

FUNK = ConductorPlan(
    style="funk",
    intensity=84,
    drums="driving",
    bass="syncopated",
    piano="syncopated",
)


def test_conductor_plan_validates_and_rules_preserve_unspecified_fields():
    for invalid in ({"style": "rock"}, {"intensity": 101}, {"intensity": "40"}, {"notes": []}):
        with pytest.raises(ValidationError):
            ConductorPlan.model_validate(invalid)
    initial = ConductorPlan()
    assert rule_plan("把强度提高 20 点", initial).intensity == 75
    assert rule_plan("忘记今天的天气", initial) == initial
    assert rule_plan("先不要放克，钢琴用长和弦", initial) == ConductorPlan(piano="sustained")
    changed = rule_plan("改成波萨诺瓦，35%，鼓组稀一点，贝斯切分，钢琴长和弦", initial)
    assert changed == ConductorPlan(
        style="bossa",
        intensity=35,
        drums="sparse",
        bass="syncopated",
        piano="sustained",
    )


def test_genres_change_the_notes_and_intensity_changes_velocity():
    async def play():
        planner = Planner()
        plans = {}
        for kind in ("swing", "bossa", "funk", "ballad"):
            plan = ConductorPlan(style=kind)
            plans[kind] = await planner.plan(0, MusicSettings(conductor=plan), 1)
            fallback = planner.fallback(0, MusicSettings(conductor=plan), 1)
            assert set(fallback.fallback_tracks) == set(active_tracks("trio"))
        quiet = await planner.plan(0, MusicSettings(conductor=ConductorPlan(intensity=12)), 1)
        loud = await planner.plan(0, MusicSettings(conductor=ConductorPlan(intensity=92)), 1)
        return plans, quiet, loud

    plans, quiet, loud = asyncio.run(play())
    for track in TRACKS:
        scores = {
            tuple(
                (note.start_tick, note.pitch, note.duration_ticks)
                for note in plan.notes
                if note.track_id == track
            )
            for plan in plans.values()
        }
        assert len(scores) == len(plans)
    for a, b in zip(quiet.notes, loud.notes, strict=True):
        assert a.event_id == b.event_id
        assert a.velocity < b.velocity
    assert any(
        note.track_id == "drums" and note.pitch == 36 and note.start_tick % PPQ
        for note in plans["bossa"].notes
    )


def test_provider_retries_transient_http_and_validates_five_high_level_fields(monkeypatch):
    payloads = []
    valid = FUNK.model_dump()
    outputs = [
        httpx2.Response(503),
        httpx2.Response(
            200,
            json={
                "model": "served-revision",
                "choices": [{"message": {"content": json.dumps(valid)}}],
            },
        ),
        httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({**valid, "notes": [60]})}}],
            },
        ),
        httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({**valid, "intensity": 150})}}],
            },
        ),
    ]

    def response(request):
        payloads.append(json.loads(request.content))
        return outputs.pop(0)

    original = httpx2.AsyncClient

    def client_factory(**kwargs):
        return original(transport=httpx2.MockTransport(response), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", client_factory)
    engine = Conductor("http://127.0.0.1:10109/v1", "test-model", timeout=2)
    first = asyncio.run(engine.propose("放克，强度 84%", ConductorPlan()))
    assert first.source == "llm" and first.attempts == 2 and first.model == "served-revision"
    assert first.plan == FUNK
    schema = payloads[0]["response_format"]["json_schema"]["schema"]
    assert not schema["additionalProperties"] and set(schema["required"]) == valid.keys()
    next_plan = asyncio.run(engine.propose("抒情一点，强度 25%", FUNK))
    assert next_plan.source == "rules" and next_plan.reason == "invalid_response"
    assert next_plan.plan.style == "ballad" and next_plan.plan.intensity == 25


def test_inference_wait_does_not_hold_transport_and_activates_at_reported_bar(tmp_path):
    started = Event()
    finish = Event()

    class BlockingConductor(Conductor):
        async def _request(self, client, instruction, current):
            started.set()
            while not finish.is_set():
                await asyncio.sleep(0.01)
            return FUNK, "test-revision"

    conductor = BlockingConductor("http://127.0.0.1:10109/v1", "test-model", timeout=4)
    with TestClient(create_app(tmp_path, conductor=conductor), base_url="http://localhost") as api:
        state = api.post("/sessions", json={"bpm": 240}).json()
        prefix = f"/sessions/{state['id']}"
        assert api.post(f"{prefix}/conduct", json={"instruction": " "}).status_code == 422
        assert api.post(f"{prefix}/play").json()["transport"] == "playing"
        with ThreadPoolExecutor(1) as background:
            future = background.submit(api.post, f"{prefix}/conduct", json={"instruction": "放克"})
            try:
                assert started.wait(2)
                duplicate = api.post(f"{prefix}/conduct", json={"instruction": "抒情"})
                assert duplicate.status_code == 429
                assert api.post(f"{prefix}/pause").json()["transport"] == "paused"
                assert api.post(f"{prefix}/play").json()["transport"] == "playing"
                assert not future.done()
            finally:
                finish.set()
            result = future.result(timeout=5).json()
        assert result["conductor"]["status"] == "scheduled" and result["bar"] == 0
        assert result["settings"]["conductor"]["style"] == "swing"
        target = result["conductor"]["effective_bar"]
        assert result["pending"]["bar"] == target
        until = time.monotonic() + 3
        while result["conductor"]["status"] == "scheduled" and time.monotonic() < until:
            time.sleep(0.02)
            result = api.get(f"{prefix}/state").json()
        assert result["bar"] == target and result["conductor"]["status"] == "applied"
        assert result["settings"]["conductor"] == FUNK.model_dump()
        api.post(f"{prefix}/stop")

    events = [
        json.loads(line)
        for line in (tmp_path / "sessions" / f"{state['id']}.jsonl").read_text().splitlines()
    ]
    audit = next(event for event in events if event["type"] == "conductor")
    assert (audit["prompt_version"], audit["model"], audit["attempts"]) == (
        PROMPT_VERSION,
        "test-revision",
        1,
    )
    assert audit["effective_bar"] == target and audit["plan"] == FUNK.model_dump()
    assert any(
        event["type"] == "bar"
        and event["bar"] == target
        and event["state_version"] == audit["state_version"]
        for event in events
    )


def test_total_timeout_falls_back_and_next_request_can_use_model():
    async def exercise():
        class SometimesSlow(Conductor):
            calls = 0

            async def _request(self, client, instruction, current):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(1)
                return FUNK, "test-model"

        conductor = SometimesSlow("http://127.0.0.1:10109/v1", "test-model", timeout=0.08)
        first = await conductor.propose("抒情一点，强度 20%", ConductorPlan())
        assert first.reason == "timeout" and first.source == "rules"
        assert first.plan == ConductorPlan(
            style="ballad",
            intensity=20,
            drums="sparse",
            bass="root",
            piano="sustained",
        )
        second = await conductor.propose("放克", first.plan)
        assert second.source == "llm" and second.plan == FUNK

    asyncio.run(exercise())
