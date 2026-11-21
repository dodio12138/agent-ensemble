"""Instrument behavior for extended conductor profiles."""

import asyncio
from itertools import product

from band.music import PPQ, ConductorPlan, MusicProfile, MusicSettings, PlayerPhrase
from band.planner import Planner


def _settings(genre, groove, **approaches):
    profile = MusicProfile(
        genre=genre, groove=groove, tonic="C", mode="major", harmony="keep", basis="genre"
    )
    return MusicSettings(
        conductor=ConductorPlan(profile=profile, **approaches),
        progression=("C", "Dbm7", "D/F#", "F7", "F#maj9", "Ab7#9", "Bbmaj7", "B"),
        collaboration="shared",
    )


def _part(plan, track):
    return tuple(note for note in plan.notes if note.track_id == track)


def _drum_ticks(plan, pitch):
    return {note.start_tick for note in _part(plan, "drums") if note.pitch == pitch}


def test_reggae_anchor_and_offbeat_accompaniment_survive_fallback_and_live_midi():
    async def verify():
        planner = Planner()
        reggae = _settings("reggae", "one_drop")
        player = PlayerPhrase(pitch_register="low", activity="busy", accent_tick=PPQ * 3)
        for plan in (
            await planner.plan(0, reggae, 1),
            planner.fallback(0, reggae, 1),
            await planner.plan(0, reggae, 1, player_phrase=player),
        ):
            assert _drum_ticks(plan, 36) == _drum_ticks(plan, 37) == {2 * PPQ}
            assert min(note.start_tick for note in _part(plan, "bass")) == PPQ // 2
            assert all(note.start_tick % PPQ == PPQ // 2 for note in _part(plan, "piano"))

    asyncio.run(verify())


def test_disco_octave_bass_and_hard_rock_open_fifth_are_distinct_from_pop():
    async def verify():
        planner = Planner()
        disco = await planner.plan(0, _settings("disco", "four_on_floor"), 1)
        assert _drum_ticks(disco, 36) == {0, PPQ, 2 * PPQ, 3 * PPQ}
        pitches = [note.pitch for note in _part(disco, "bass")]
        assert [pitch - pitches[0] for pitch in pitches] == [0, 12] * 4

        rock = await planner.plan(0, _settings("hard_rock", "straight_eighth"), 1)
        pop = await planner.plan(0, _settings("pop", "straight_eighth"), 1)
        assert _drum_ticks(rock, 38) == {PPQ, 3 * PPQ}
        assert len(_part(rock, "bass")) > len(_part(pop, "bass"))
        assert {note.pitch % 12 for note in _part(rock, "piano")} == {0, 7}
        assert {note.pitch % 12 for note in _part(pop, "piano")} == {0, 4, 7}

    asyncio.run(verify())


def test_grooves_keep_every_musician_in_range_across_harmonies_and_player_registers():
    async def verify():
        planner = Planner()
        for groove, register in product(
            (
                "swing",
                "bossa",
                "straight_eighth",
                "sixteenth",
                "shuffle",
                "one_drop",
                "four_on_floor",
                "half_time",
            ),
            ("low", "middle", "high"),
        ):
            song = _settings("hard_rock", groove, drums="driving", piano="syncopated")
            player = PlayerPhrase(pitch_register=register, activity="sparse", accent_tick=0)
            for bar in range(8):
                plan = await planner.plan(bar, song, 1, player_phrase=player)
                assert not plan.fallback_tracks
                chord = song.chord_at(bar)
                chord_tones = {(chord.root + interval) % 12 for interval in chord.intervals}
                assert _part(plan, "drums")
                assert {note.pitch % 12 for note in _part(plan, "piano")} <= chord_tones
                assert {note.pitch % 12 for note in _part(plan, "bass")} <= chord_tones | {
                    chord.bass
                }

    asyncio.run(verify())
