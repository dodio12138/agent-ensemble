import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from band.api import create_app
from band.conductor import Conductor, ConductResult
from band.harmony import materialize_harmony
from band.improv_arrangement import bind_improvisation
from band.music import HARMONY_MODES, ConductorPlan, MusicProfile, MusicSettings, parse_chord
from band.planner import Planner
from band.timing import Scheduler

REGGAE = MusicProfile(
    genre="reggae",
    groove="one_drop",
    mode="mixolydian",
    harmony="mixolydian_174",
    tonic="E♭",
    basis="genre",
)


def test_chord_formulas_transpose_and_spell_the_degrees():
    assert materialize_harmony(REGGAE) == ("Eb", "Eb", "Db", "Ab") * 2
    for formula, mode in HARMONY_MODES.items():
        for tonic in ("A", "Db", "F#", "Bb"):
            data = REGGAE.model_dump()
            profile = MusicProfile.model_validate(
                {**data, "harmony": formula, "mode": mode, "tonic": tonic}
            )
            chords = materialize_harmony(profile)
            assert len(chords) == 8
            assert [parse_chord(chord).symbol for chord in chords] == list(chords)


def test_groove_changes_use_pickup_even_with_same_base_preset():
    scheduler = Scheduler(Planner(), clock=lambda: 0, threaded=False)
    try:
        scheduler.play()
        pending = scheduler.stage_settings(
            {
                "conductor": ConductorPlan(profile=REGGAE).model_dump(),
                "progression": materialize_harmony(REGGAE),
            }
        )["pending"]
        assert pending["pickup_bar"] == 1 and pending["bar"] == 2
        assert pending["transition"]["from_style"] == pending["transition"]["to_style"]
        assert pending["transition"]["to_groove"] == "one_drop"
        assert pending["transition"]["next_chord"] == "Db"
        assert pending["settings"]["progression"] == ["Eb", "Eb", "Db", "Ab"] * 2
        assert scheduler.snapshot()["settings"]["conductor"].get("profile") is None
    finally:
        scheduler.close()


def test_in_flight_profile_does_not_overwrite_manual_chord_edits(tmp_path):
    started, release = Event(), Event()

    class WaitingConductor(Conductor):
        async def propose(self, instruction, current):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return ConductResult(
                plan=ConductorPlan(profile=REGGAE),
                source="rules",
                progression=materialize_harmony(REGGAE),
            )

    with TestClient(
        create_app(tmp_path, conductor=WaitingConductor()), base_url="http://localhost"
    ) as client:
        session_id = client.post("/sessions", json={}).json()["id"]
        path = f"/sessions/{session_id}"
        chords = ["Em", "D", "C", "B7"] * 2
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(client.post, f"{path}/conduct", json={"instruction": "雷鬼"})
            try:
                assert started.wait(2)
                manual = client.patch(f"{path}/settings", json={"progression": chords})
                assert manual.status_code == 200
            finally:
                release.set()
            result = future.result(2).json()
        assert result["conductor"]["reason"] == "superseded"
        assert result["settings"]["progression"] == chords
        assert result["settings"]["conductor"].get("profile") is None


def test_intentional_borrowed_chord_comes_back_to_the_written_tonic():
    settings = MusicSettings(progression=("C", "F", "C", "Am", "Dm", "F", "G7", "C"))
    result = asyncio.run(Conductor().improvise("用借用和弦做一点反套路", settings, 0))
    moves = result.plan.harmonic_moves
    assert len(moves) == 2
    unexpected = [move for move in moves if move.technique in {"borrowed", "tritone", "deceptive"}]
    assert len(unexpected) == 1
    (borrowed,) = unexpected
    assert borrowed.chord == "Fm"
    assert result.plan.progression[borrowed.position + 1] == "C"
    assert set(parse_chord("Fm").intervals) == {0, 3, 7}
    assert moves[1].position - moves[0].position >= 2

    start = borrowed.position + 8
    bound = bind_improvisation(result.plan, settings, start, start + 8)
    assert bound.relative_to_start
    assert bound.chord_for_bar(start, start) == "F"
    assert bound.chord_for_bar(start + 1, start) == "C"
    assert all(move.technique != "borrowed" for move in bound.harmonic_moves)


def test_scheduled_improvisation_uses_actual_theme_cycle_and_returns_smoothly():
    settings = MusicSettings(progression=("C", "F", "C", "Am", "Dm", "F", "G7", "C"))
    result = asyncio.run(Conductor().improvise("来一点反套路", settings, 2))
    scheduler = Scheduler(Planner(), settings=settings, clock=lambda: 0, threaded=False)
    try:
        scheduler.play()
        segment = scheduler.schedule_improvisation(result.plan, 2, settings, {"source": "rules"})
        assert segment.start_bar == 1
        first, last = segment.start_bar, segment.end_bar - 1
        assert segment.fallback[0].harmony == settings.progression[first]
        assert segment.fallback[-1].harmony == segment.plan.chord_for_bar(last, first)
        _, _, _, options = scheduler.arrangement_for(last, settings)
        assert options["next_chord"] == settings.chord_at(last + 1)
    finally:
        scheduler.close()
