"""Voicings and bass pickups follow the audible harmony across bars."""

import asyncio

from band.music import MusicSettings, parse_chord
from band.planner import Planner


def _pitches(plan, track):
    return tuple(note.pitch for note in plan.notes if note.track_id == track)


def test_connected_piano_retains_and_moves_guide_tones_in_ready_and_fallback_bars():
    async def verify():
        planner = Planner()
        song = MusicSettings(progression=("Dm7", "G7", "Cmaj7", "Am7") * 2, collaboration="shared")
        core = []
        for bar in range(3):
            ready = await planner.plan(bar, song, 1, voice_leading=True)
            fallback = planner.fallback(bar, song, 1, voice_leading=True)
            shell = set(_pitches(fallback, "piano"))
            assert shell <= set(_pitches(ready, "piano"))
            core.append(shell)
        ii, v, i = core
        (held_f,) = ii & v
        (held_b,) = v & i
        assert (held_f % 12, held_b % 12) == (5, 11)
        assert abs(next(iter(ii - v)) - held_b) == 1
        assert abs(held_f - next(iter(i - v))) == 1

    asyncio.run(verify())


def test_actual_following_slash_bass_guides_the_pickup():
    async def verify():
        planner = Planner()
        song = MusicSettings(progression=("Am7", "Dm7", "G7", "Cmaj7") * 2, collaboration="shared")
        arriving = song.model_copy(update={"progression": ("Am7", "G/B", "G7", "Cmaj7") * 2})
        slash = parse_chord("G/B")
        for before, after in (
            (
                planner.fallback(0, song, 1, voice_leading=True, next_chord=slash),
                planner.fallback(1, arriving, 1, voice_leading=True),
            ),
            (
                await planner.plan(0, song, 1, voice_leading=True, next_chord=slash),
                await planner.plan(1, arriving, 1, voice_leading=True),
            ),
        ):
            arrival = _pitches(after, "bass")[0]
            assert arrival % 12 == slash.bass
            assert abs(_pitches(before, "bass")[-1] - arrival) == 1

    asyncio.run(verify())
