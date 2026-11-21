from band.music import TRACKS

"""Musical responses to player phrases, independent of the MIDI device."""

import asyncio
from itertools import product

from band.music import (
    PPQ,
    GrooveTransition,
    MusicSettings,
    PlayerPhrase,
    validate_notes,
)
from band.planner import Planner
from band.transition import apply_transition


def _part(plan, track):
    return tuple(note for note in plan.notes if note.track_id == track)


def test_busy_low_player_phrase_changes_all_three_parts():
    async def compare():
        planner = Planner()
        song = MusicSettings(density=0.9)
        phrase = PlayerPhrase(pitch_register="low", activity="busy", accent_tick=PPQ // 2)
        original = await planner.plan(3, song, 2)
        reacting = await planner.plan(3, song, 2, player_phrase=phrase)
        assert (await planner.plan(3, song, 2)) == original
        return original, reacting, phrase

    original, reacting, phrase = asyncio.run(compare())
    assert original.player_response is None
    assert reacting.player_response.phrase == phrase
    assert reacting.player_response.model_dump(mode="json")["phrase"]["register"] == "low"
    assert len(_part(reacting, "drums")) < len(_part(original, "drums"))
    bass = _part(reacting, "bass")
    assert {note.start_tick for note in bass} == {0, PPQ * 2}
    assert max(note.pitch for note in bass) <= 41
    piano = _part(reacting, "piano")
    assert len({note.start_tick for note in piano}) == 1
    assert max(note.duration_ticks for note in piano) <= PPQ // 2
    assert min(note.pitch for note in piano) >= 65
    assert reacting.negotiation


def test_sparse_accent_receives_rhythmic_answer_and_register_aware_voicing():
    async def play():
        planner = Planner()
        song = MusicSettings(density=0.4, collaboration="shared")
        high = PlayerPhrase(
            pitch_register="high", activity="sparse", accent_tick=0, velocity="strong"
        )
        low = high.model_copy(update={"pitch_register": "low"})
        return (
            await planner.plan(0, song, 1, player_phrase=high),
            await planner.plan(0, song, 1, player_phrase=low),
        )

    high, low = asyncio.run(play())
    assert any(
        note.start_tick == 2 * PPQ // 3 and note.pitch == 37 for note in _part(high, "drums")
    )
    assert any(note.start_tick == 2 * PPQ // 3 for note in _part(high, "bass"))
    assert max(note.pitch for note in _part(high, "piano")) < max(
        note.pitch for note in _part(low, "piano")
    )


def test_player_responses_preserve_harmony_ranges_and_transition_metadata():
    async def verify():
        planner = Planner()
        song = MusicSettings(
            progression=("Bbmaj7/F", "Dm7", "A7#9", "C6", "Abmaj9", "Gm7", "F7", "C")
        )
        for bar, register, activity in product(
            range(8), ("low", "middle", "high"), ("busy", "sparse")
        ):
            chord = song.chord_at(bar)
            phrase = PlayerPhrase(pitch_register=register, activity=activity, accent_tick=3 * PPQ)
            draft = await planner.plan(bar, song, 1, player_phrase=phrase)
            allowed = {(chord.root + interval) % 12 for interval in chord.intervals}
            assert {part.track_id for part in draft.notes} - {"vocal"} == TRACKS.keys()
            assert validate_notes(bar, draft.notes) == draft.notes
            assert all(note.pitch % 12 in allowed | {chord.bass} for note in _part(draft, "bass"))
            assert all(note.pitch % 12 in allowed for note in _part(draft, "piano"))
            transition = GrooveTransition(
                phase="pickup",
                from_style="swing",
                to_style="bossa",
                from_bpm=120,
                to_bpm=140,
            )
            assert (
                apply_transition(draft, song, transition).player_response == draft.player_response
            )

    asyncio.run(verify())
