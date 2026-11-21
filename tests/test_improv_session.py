import asyncio
import time
from threading import Event

from fastapi.testclient import TestClient
from test_engine import Clock

from band.api import create_app
from band.conductor import Conductor, ImprovisationResult
from band.music import ImprovisationPhrase, ImprovisationPlan, MusicSettings
from band.planner import Planner
from band.timing import Scheduler

THEME = MusicSettings()
VARIATION = ImprovisationPlan(
    progression=("Dm7", "G7", "Cmaj9", "Am7", "Dm7", "G7", "Em7", "Eb7"),
    phrases=(
        ImprovisationPhrase(lead="piano", character="space"),
        ImprovisationPhrase(lead="bass", character="answer"),
        ImprovisationPhrase(lead="drums", character="build"),
        ImprovisationPhrase(lead="piano", character="resolve"),
    ),
)


def wait(client, prefix, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = client.get(f"{prefix}/state").json()
        if predicate(state):
            return state
        time.sleep(0.025)
    raise AssertionError(state["improvisation"])


def test_presets_apply_without_contacting_llm(tmp_path):
    class RuleOnly(Conductor):
        async def propose(self, *args):
            raise AssertionError("Presets should apply directly")

    with TestClient(create_app(tmp_path, conductor=RuleOnly()), base_url="http://localhost") as api:
        sid = api.post("/sessions").json()["id"]
        preset = api.post(f"/sessions/{sid}/preset", json={"style": "bossa"}).json()
        assert preset["settings"]["conductor"]["style"] == "bossa"
        assert preset["settings"]["conductor"]["bass"] == "root"
        assert preset["conductor"]["source"] == "preset"
        assert preset["conductor"]["attempts"] == 0
        reggae = api.post(f"/sessions/{sid}/preset", json={"style": "reggae"}).json()
        profile = reggae["settings"]["conductor"]["profile"]
        assert profile["genre"] == "reggae" and profile["groove"] == "one_drop"
        assert reggae["conductor"]["source"] == "preset"
        assert reggae["settings"]["progression"] != preset["settings"]["progression"]
        assert api.post(f"/sessions/{sid}/preset", json={"style": "polka"}).status_code == 422


def test_improvised_chorus_uses_harmonic_fallback_and_returns_to_theme():
    clock = Clock()
    scheduler = Scheduler(Planner(), clock=clock, threaded=False)
    try:
        assert scheduler.play()["current_bar"] == 0
        scheduled = scheduler.schedule_improvisation(VARIATION, 1, THEME, {"source": "rules"})
        assert scheduled is not None
        assert (scheduled.start_bar, scheduled.end_bar) == (1, 9)
        plans = {}
        while scheduler.snapshot()["current_bar"] < scheduled.end_bar:
            state = clock.step(scheduler)
            assert tuple(state["settings"]["progression"]) == THEME.progression
            plan = next(p for p in state["plans"] if p["bar"] == state["current_bar"])
            plans[state["current_bar"]] = plan
        for bar in range(1, 9):
            assert plans[bar]["harmony"] == VARIATION.progression[bar % 8]
            assert plans[bar]["improvisation_id"] == 1
            assert plans[bar]["fallback_tracks"]
        assert plans[9]["improvisation_id"] is None
        assert scheduler.snapshot()["improvisation_active"] is None
    finally:
        scheduler.close()


def test_improvise_while_playing_and_apply_only_at_announced_bar(tmp_path):
    began, release = Event(), Event()

    class Delayed(Conductor):
        async def improvise(self, instruction, theme, variant):
            began.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return ImprovisationResult(
                plan=VARIATION,
                source="llm",
                model="test-director",
                attempts=1,
            )

    with TestClient(create_app(tmp_path, conductor=Delayed()), base_url="http://localhost") as api:
        sid = api.post("/sessions", json={"bpm": 240}).json()["id"]
        prefix = f"/sessions/{sid}"
        api.post(f"{prefix}/play")
        started = api.post(f"{prefix}/improvisation", json={"instruction": "贝斯答一句"})
        assert started.json()["improvisation"]["planning"]
        assert began.wait(1)
        assert api.post(f"{prefix}/improvisation", json={}).status_code == 429
        assert api.post(f"{prefix}/pause").json()["transport"] == "paused"
        assert api.post(f"{prefix}/play").json()["transport"] == "playing"
        release.set()
        ready = wait(api, prefix, lambda s: s["improvisation"]["pending"] is not None)
        cue = ready["improvisation"]["pending"]
        assert cue["source"] == "llm" and cue["start_bar"] > ready["bar"]
        active = wait(api, prefix, lambda s: s["improvisation"]["active"] is not None)
        assert active["bar"] == cue["start_bar"]
        plan = next(p for p in active["plans"] if p["bar"] == active["bar"])
        assert plan["harmony"] == VARIATION.progression[active["bar"] % 8]
        assert plan["improvisation_phrase"]["lead"] == "piano"
        assert active["transport"] == "playing"
        assert api.post(f"{prefix}/stop").json()["improvisation"]["active"] is None


def test_automatic_improvisation_queues_next_chorus_and_can_be_disabled(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as api:
        sid = api.post("/sessions", json={"bpm": 240}).json()["id"]
        prefix = f"/sessions/{sid}"
        assert api.patch(f"{prefix}/improvisation", json={"auto": True}).json()["improvisation"][
            "auto"
        ]
        api.post(f"{prefix}/play")
        state = wait(
            api,
            prefix,
            lambda s: bool(s["improvisation"]["active"] and s["improvisation"]["pending"]),
            10,
        )
        active, pending = state["improvisation"]["active"], state["improvisation"]["pending"]
        assert pending["start_bar"] == active["end_bar"]
        assert pending["source"] == "rules" and pending["automatic"]
        result = api.patch(f"{prefix}/improvisation", json={"auto": False}).json()
        assert result["improvisation"]["active"]
        assert result["improvisation"]["pending"] is None
        assert result["transport"] == "playing"
