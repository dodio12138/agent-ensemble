"""Compact, bounded notation for reviewing model-written melodic phrases."""

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from band.music import MELODY_STEPS_PER_QUARTER, FrozenModel, parse_chord

STEPS_PER_QUARTER = MELODY_STEPS_PER_QUARTER
MAX_PHRASE_NOTES = 16
PHRASE_PITCH_RANGE = (48, 95)

PhraseNote = Annotated[list[int], Field(min_length=4, max_length=4)]


class PhraseDraft(FrozenModel):
    """Each row is [start, duration, MIDI pitch, velocity] on a quarter-note grid."""

    notes: list[PhraseNote] = Field(min_length=1, max_length=MAX_PHRASE_NOTES)

    @classmethod
    def output_schema(cls) -> dict:
        return cls.model_json_schema()


def phrase_issues(
    draft: PhraseDraft,
    chords: Sequence[str],
    next_chord: str | None,
    steps_per_bar: int,
    *,
    allow_anticipation: bool = False,
    allow_cross_bar: bool = False,
    pitch_range: tuple[int, int] = PHRASE_PITCH_RANGE,
) -> list[str]:
    """Report bounded, fixable notation problems before a phrase becomes playable."""
    if not chords or steps_per_bar <= 0:
        raise ValueError("Phrase needs chord symbols and a positive bar length")
    if not PHRASE_PITCH_RANGE[0] <= pitch_range[0] <= pitch_range[1] <= PHRASE_PITCH_RANGE[1]:
        raise ValueError("Phrase pitch range is outside the supported register")

    harmonies = [parse_chord(chord) for chord in chords]
    limit = steps_per_bar * len(chords)
    low, high = pitch_range
    cursor = 0
    issues = []
    for number, (start, duration, pitch, velocity) in enumerate(draft.notes, 1):
        if start < 0 or duration <= 0 or start + duration > limit:
            issues.append(f"Note {number}: stay within steps 0–{limit - 1} with positive duration.")
        elif (
            not allow_cross_bar
            and start // steps_per_bar != (start + duration - 1) // steps_per_bar
        ):
            issues.append(f"Note {number}: shorten duration or split the note at the bar line.")
        if start < cursor and start >= 0:
            issues.append(
                f"Note {number}: keep notes sorted and monophonic; start at or after step {cursor}."
            )
        if not low <= pitch <= high:
            issues.append(f"Note {number}: choose MIDI pitch {low}–{high}.")
        if not 1 <= velocity <= 127:
            issues.append(f"Note {number}: choose a velocity from 1 to 127.")
        cursor = max(cursor, start + duration)
    if issues:
        return issues

    start, duration, pitch, _ = draft.notes[-1]
    first_bar, last_bar = start // steps_per_bar, (start + duration - 1) // steps_per_bar
    cadence_chords = harmonies[first_bar : last_bar + 1]
    chord_tones = [
        {(chord.root + interval) % 12 for interval in chord.intervals} for chord in cadence_chords
    ]
    allowed = set.intersection(*chord_tones)
    if pitch % 12 in allowed:
        return []
    if allow_anticipation and next_chord is not None and limit - 4 <= start < limit:
        incoming = parse_chord(next_chord)
        if (pitch - incoming.root) % 12 in {interval % 12 for interval in incoming.intervals}:
            return []
    names = " and ".join(chord.symbol for chord in cadence_chords)
    choices = sorted(
        (candidate for candidate in range(low, high + 1) if candidate % 12 in allowed),
        key=lambda candidate: (abs(candidate - pitch), candidate),
    )[:3]
    if choices:
        return [
            f"Resolve the final note over {names}; nearby chord tones: {', '.join(map(str, choices))}."
        ]
    return [
        f"Shorten the final note to land within one chord; no shared chord tone over {names} in range."
    ]
