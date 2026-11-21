"""Constrained relative-time notation for the local conductor's melody drafts."""

import json
from functools import cache, lru_cache

from band.music import parse_chord
from band.phrase_score import STEPS_PER_QUARTER, PhraseDraft

GRID_STEPS = STEPS_PER_QUARTER // 8
GAPS = (0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20)
LENGTHS = (2, 3, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28)
VELOCITIES = (57, 64, 71, 77, 84, 91, 98, 105)

PHRASE_PROMPT = """Compose an original two-bar monophonic lead melody for the requested style.
Each bar has notes gap/duration/MIDI_pitch/velocity. Gap is counted from the previous
note's end (from the bar start on the first note). Units are eighths of a quarter note.
Duration 8 is one quarter note, 4 is an eighth note. "|" starts the second bar.
Keep each bar within the given time budget. An unfilled end of bar is silent.
Write a recognizable short idea and a related answer with different spacing and lengths.
Choose a point of emphasis, with quieter notes around it. Use more than two pitches.
Use the current chords and the upcoming chord to decide how to end and lead into
the next phrase. If prior pitches are given, echo a small part of their shape when fitting.
Return the notation only."""


def chord_pitches(symbol: str, pitch_range: tuple[int, int]) -> tuple[int, ...]:
    chord = parse_chord(symbol)
    pitch_classes = {(chord.root + interval) % 12 for interval in chord.intervals}
    return tuple(
        pitch for pitch in range(pitch_range[0], pitch_range[1] + 1) if pitch % 12 in pitch_classes
    )


def song_scale(tonic: str, mode: str) -> frozenset[int]:
    intervals = {
        "major": (0, 2, 4, 5, 7, 9, 11),
        "minor": (0, 2, 3, 5, 7, 8, 10),
        "dorian": (0, 2, 3, 5, 7, 9, 10),
        "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    }
    return frozenset((parse_chord(tonic).root + step) % 12 for step in intervals[mode])


def _alternatives(values: tuple[int, ...]) -> str:
    return " | ".join(json.dumps(str(value)) for value in values)


@lru_cache(maxsize=3)
def _rhythm_rules(units_per_bar: int) -> str:
    rules: dict[str, str] = {}

    @cache
    def enter(
        side: str, dynamic: str, room: int, played: int, short: bool, pause: bool
    ) -> str | None:
        label = f"{side}{dynamic}{room}n{played}s{int(short)}p{int(pause)}"
        options = []
        for gap in GAPS:
            for length in LENGTHS:
                remaining = room - gap - length
                if remaining < 0:
                    continue
                has_short = short or length <= 6
                has_pause = side == "opening" and (pause or gap >= 3)
                prefix = f'"{gap}/{length}/"'
                if played >= 1:
                    if side == "opening" and has_short and has_pause:
                        options.append(f'{prefix} openingpitch "/" {dynamic}')
                    if side == "answer" and length >= 10 and (has_short or played >= 2):
                        options.append(f'{prefix} endingpitch "/" {dynamic}')
                if played < 3 and remaining >= 4:
                    branch = enter(side, dynamic, remaining, played + 1, has_short, has_pause)
                    if branch:
                        options.append(f'{prefix} {side}pitch "/" velocity " " {branch}')
        if options:
            rules[label] = " | ".join(options)
            return label
        return None

    for side in ("opening", "answer"):
        for dynamic in ("soft", "strong"):
            if not enter(side, dynamic, units_per_bar, 0, False, False):
                raise ValueError("The meter is too short for a two-bar melody")
    return "\n".join(f"{label} ::= {rule}" for label, rule in rules.items())


def phrase_grammar(
    steps_per_bar: int,
    pitch_range: tuple[int, int],
    scale: frozenset[int],
    chords: tuple[str, str],
) -> str:
    units = steps_per_bar // GRID_STEPS
    low, high = pitch_range
    diatonic = {pitch for pitch in range(low, high + 1) if pitch % 12 in scale}
    legal_pitches = tuple(
        tuple(sorted(diatonic.union(chord_pitches(chord, pitch_range)))) for chord in chords
    )
    # Either bar can have the stronger ending. The model selects the direction
    # when it writes the two velocities, with all interior velocities still free.
    start = f"{units}n0s0p0"
    root = (
        f'root ::= openingsoft{start} "|" answerstrong{start}'
        f' | openingstrong{start} "|" answersoft{start}'
    )
    return "\n".join(
        (
            root,
            f"openingpitch ::= {_alternatives(legal_pitches[0])}",
            f"answerpitch ::= {_alternatives(legal_pitches[1])}",
            f"endingpitch ::= {_alternatives(chord_pitches(chords[1], pitch_range))}",
            f"velocity ::= {_alternatives(VELOCITIES)}",
            f"soft ::= {_alternatives(VELOCITIES[:4])}",
            f"strong ::= {_alternatives(VELOCITIES[-3:])}",
            _rhythm_rules(units),
        )
    )


def decode_phrase(raw: str, steps_per_bar: int) -> PhraseDraft:
    bars = raw.strip().split("|")
    if len(bars) != 2 or any(not bar.strip() for bar in bars):
        raise ValueError("Expected two bars of phrasing")
    notes = []
    for index, bar in enumerate(bars):
        tick = index * steps_per_bar
        for token in bar.split():
            gap, length, pitch, velocity = map(int, token.split("/"))
            if gap < 0 or length <= 0:
                raise ValueError("Invalid rest or note length")
            tick += gap * GRID_STEPS
            duration = length * GRID_STEPS
            notes.append([tick, duration, pitch, velocity])
            tick += duration
            if tick > (index + 1) * steps_per_bar:
                raise ValueError("The note ends past the bar line")
    return PhraseDraft(notes=notes)
