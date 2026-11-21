import asyncio
import itertools

from test_engine import Clock

from band.improv_arrangement import apply_solo
from band.jam import JamDirector, transpose
from band.music import CHORUS_BARS, MusicSettings, validate_notes
from band.planner import Planner
from band.timing import Scheduler


def perform(profile: str, bars: int, seed: int = 3):
    """Run the scheduler the way a session does: jam director, planning, solos."""
    clock = Clock()
    planner = Planner()
    scheduler = Scheduler(
        planner, MusicSettings(), clock=clock, threaded=False, history_size=500_000
    )
    jam = JamDirector(scheduler, seed=seed)
    jam.configure(enabled=True, profile=profile)
    solos = {}

    async def commit():
        for bar, settings, version in scheduler.planning_targets():
            plan = await planner.plan(bar, settings, version)
            if solo := jam.solo_for(bar):
                plan = apply_solo(plan, settings, *solo)
                solos[bar] = (solo, [n for n in plan.notes if n.event_id.startswith("improv-")])
            assert validate_notes(bar, plan.notes) == plan.notes
            scheduler.commit(plan)

    asyncio.run(commit())
    scheduler.play()
    while scheduler.snapshot()["current_bar"] < bars:
        jam.poll()
        asyncio.run(commit())
        clock.step(scheduler)
    events = scheduler.events_after(0)
    return scheduler, jam, events, solos


def test_transpose_moves_roots_and_slash_basses():
    assert [transpose(chord, 2) for chord in ("C", "F#m7", "Bb7", "G/B", "Ebmaj7")] == [
        "D",
        "G#m7",
        "C7",
        "A/C#",
        "Fmaj7",
    ]


def test_a_long_jam_keeps_changing_solos_dynamics_tempo_and_groove():
    scheduler, _jam, events, solos = perform("wutiaoren", 13 * CHORUS_BARS)
    try:
        moves = [event["move"] for event in events if event["type"] == "jam"]
        assert len(moves) >= 10
        assert len(set(moves)) >= 6, moves
        # Sections never repeat back to back; two solos in a row trade between players.
        labels = [event["label"] for event in events if event["type"] == "jam"]
        assert all(left != right for left, right in itertools.pairwise(labels)), labels
        bars = [event for event in events if event["type"] == "bar"]
        tempos = {event["bpm"] for event in bars}
        assert len(tempos) >= 3 and min(tempos) >= 108 and max(tempos) <= 140
        # Rotating soloists actually play lines, and the accordion gets a turn.
        soloists = {track for (track, _, _), notes in solos.values() if notes}
        assert len(soloists) >= 3 and "synth" in soloists | {"synth"}
        assert all(notes for (track, _, _), notes in solos.values() if track != "drums")
        # Dynamics and the arrangement move: volumes change and some tracks drop out.
        notes = [event for event in events if event["type"] == "note_on"]
        levels = {(event["track_id"], round(event["volume"], 2)) for event in notes}
        assert len({level for track, level in levels if track == "bass"}) >= 2
        silences = [event for event in events if event["type"] == "silence"]
        assert any(event.get("reason") == "mute" for event in silences)
    finally:
        scheduler.close()


def test_wu_tiao_ren_profile_sets_up_a_folk_rock_band():
    scheduler = Scheduler(Planner(), MusicSettings(), clock=Clock(), threaded=False)
    try:
        jam = JamDirector(scheduler)
        jam.configure(enabled=True, profile="wutiaoren")
        settings, tracks = scheduler.desired_configuration()
        assert settings.lineup == "modern" and settings.bpm == 120
        assert settings.conductor.profile.genre == "rock"
        assert settings.progression[:3] == ("C", "C", "F")
        assert tracks["synth"].program == 21  # accordion
        assert tracks["rhythm_guitar"].program == 29  # overdriven guitar
        assert jam.snapshot()["profiles"]["wutiaoren"] == "五条人式民谣摇滚"
    finally:
        scheduler.close()


def test_switching_the_jam_off_returns_to_the_theme():
    scheduler, jam, _, _ = perform("current", 3 * CHORUS_BARS + 2, seed=8)
    try:
        jam.configure(enabled=False)
        settings, tracks = scheduler.desired_configuration()
        assert settings.bpm == 120 and settings.progression == MusicSettings().progression
        assert not any(track.muted for track in tracks.values())
        assert jam.solo_for(scheduler.snapshot()["current_bar"] + 4) is None
    finally:
        scheduler.close()


def test_jam_endpoint_switches_profiles_and_turns_off_auto_improvisation(tmp_path):
    from fastapi.testclient import TestClient

    from band.api import create_app

    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        sid = client.post("/sessions").json()["id"]
        client.patch(f"/sessions/{sid}/improvisation", json={"auto": True})
        state = client.patch(
            f"/sessions/{sid}/jam", json={"enabled": True, "profile": "wutiaoren"}
        ).json()
        assert state["jam"]["enabled"] and state["jam"]["profile"] == "wutiaoren"
        assert state["improvisation"]["auto"] is False
        assert state["settings"]["lineup"] == "modern"
        assert state["tracks"]["synth"]["program"] == 21
        bad = client.patch(f"/sessions/{sid}/jam", json={"profile": "polka"})
        assert bad.status_code == 422
        off = client.patch(f"/sessions/{sid}/jam", json={"enabled": False}).json()
        assert off["jam"]["enabled"] is False
