"""Join a melodic line the way a player would: slides, ties and quick runs between notes.

Musicians write their notes; this layer then shapes how one note moves to the next,
in the manner of each instrument. A singer slides and adds runs (转音) into long
notes, a guitarist bends up into notes and hammers on from below, a chip voice uses
portamento, a bassist slides into notes without tying the groove together, strings
shift position between notes. Each track has a switch and a strength in the settings.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from band.music import (
    PPQ,
    TRACK_DEFINITIONS,
    Chord,
    MusicSettings,
    Note,
    TrackId,
    parse_chord,
)
from band.vocals import vocal_role

SIXTEENTH = PPQ // 4
_MODE_STEPS = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
}


@dataclass(frozen=True, slots=True)
class Flavor:
    tie: bool  # stretch notes so the line never stops between them
    runs: int  # quick notes into long notes: 1 from above, -1 from below, 0 none
    scoop: tuple[int, int]  # semitones to slide up into a note after a rest (subtle, strong)
    slide_ms: tuple[int, int]  # glide time between tied notes (subtle, strong)
    label: str


FLAVORS: dict[TrackId, Flavor] = {
    "vocal": Flavor(True, 1, (1, 2), (40, 200), "滑音、粘连和长音前的转音"),
    "lead_guitar": Flavor(True, -1, (1, 2), (30, 140), "推弦、滑弦和击弦连奏"),
    "melody": Flavor(True, 1, (1, 1), (30, 150), "滑音、连奏和装饰音"),
    "chip": Flavor(True, 0, (0, 0), (15, 110), "方波滑音"),
    "bass": Flavor(False, 0, (2, 5), (40, 160), "滑进音和换把滑音"),
    "strings": Flavor(True, 0, (0, 0), (60, 220), "换把滑音和连弓"),
    "brass": Flavor(False, 0, (1, 2), (40, 140), "唇滑和起音上滑"),
}
GLIDE_TRACKS: tuple[TrackId, ...] = tuple(FLAVORS)


def scale_classes(settings: MusicSettings, chord: Chord) -> frozenset[int]:
    """The key's pitch classes, falling back to the chord's own major or minor scale."""
    profile = settings.conductor.profile
    if profile is not None and profile.tonic:
        root, steps = parse_chord(profile.tonic).root, _MODE_STEPS[profile.mode]
    else:
        minor = 3 in chord.intervals and 4 not in chord.intervals
        root, steps = chord.root, _MODE_STEPS["minor" if minor else "major"]
    chord_tones = {(chord.root + interval) % 12 for interval in chord.intervals}
    return frozenset({(root + step) % 12 for step in steps} | chord_tones)


def _lerp(bounds: tuple[int, int], amount: float) -> int:
    low, high = bounds
    return round(low + (high - low) * amount)


def express(
    track: TrackId, settings: MusicSettings, bar: int, notes: tuple[Note, ...]
) -> tuple[Note, ...]:
    """Shape the single-note line in one track's bar; chords are left as they are."""
    flavor = FLAVORS.get(track)
    glide = getattr(settings.glide, track, None) if flavor else None
    if flavor is None or glide is None or not glide.on or glide.amount <= 0 or not notes:
        return notes
    amount = glide.amount
    # Scat is articulated: quick slides only, no scoops or runs.
    scat = track == "vocal" and vocal_role(settings) == "scat"
    definition = TRACK_DEFINITIONS[track]
    low, high = definition.min_pitch, definition.max_pitch
    ladder = [
        pitch
        for pitch in range(low, high + 1)
        if pitch % 12 in scale_classes(settings, settings.chord_at(bar))
    ]
    rng = random.Random(f"{settings.seed}:{bar}:{track}:glide")
    starts: dict[int, int] = {}
    for note in notes:
        starts[note.start_tick] = starts.get(note.start_tick, 0) + 1
    run_step = SIXTEENTH // 2 if amount >= 0.6 else SIXTEENTH * 2 // 3
    tie_gap = SIXTEENTH * (0.5 + amount)
    shaped: list[Note] = []
    line_index: int | None = None  # where the previous single note sits in shaped
    for note in sorted(notes, key=lambda item: (item.start_tick, item.pitch)):
        if starts[note.start_tick] > 1:
            shaped.append(note)  # part of a chord: it breaks the line
            line_index = None
            continue
        tick, duration, pitch = note.start_tick, note.duration_ticks, note.pitch
        previous = shaped[line_index] if line_index is not None else None
        slide_from: int | None = None
        slide_ms = _lerp(flavor.slide_ms, amount)
        if scat:
            slide_ms = round(25 + 60 * amount)
        if previous is not None:
            gap = tick - (previous.start_tick + previous.duration_ticks)
            if 0 <= gap <= tie_gap or gap < 0:
                if flavor.tie and tick > previous.start_tick:
                    shaped[line_index] = previous.model_copy(
                        update={"duration_ticks": tick - previous.start_tick}
                    )
                if previous.pitch != pitch:
                    slide_from = previous.pitch
        if slide_from is None and not scat and flavor.scoop[1] and rng.random() < 0.6 * amount:
            slide_from = pitch - (flavor.scoop[0] if amount < 0.6 else flavor.scoop[1])
            slide_ms = round(60 + 90 * amount)
        run = (
            flavor.runs
            and not scat
            and duration >= PPQ
            and pitch in ladder
            and rng.random() < (amount - 0.25) * 1.3
        )
        if run:
            position = ladder.index(pitch)
            steps = (2, 1) if flavor.runs > 0 else (-2, -1)
            if 0 <= position + steps[0] < len(ladder):
                for offset, step in enumerate(steps):
                    ornament = ladder[position + step]
                    grace = Note(
                        event_id=f"{note.event_id}-run{offset}",
                        track_id=track,
                        bar=bar,
                        start_tick=tick + offset * run_step,
                        duration_ticks=run_step,
                        pitch=ornament,
                        velocity=max(1, note.velocity - 8),
                        channel=note.channel,
                        articulation=note.articulation,
                        **({"slide_from": slide_from, "slide_ms": 35} if slide_from else {}),
                    )
                    shaped.append(grace)
                    slide_from = ornament
                tick, duration = tick + 2 * run_step, duration - 2 * run_step
                slide_ms = 45
        update: dict = {"start_tick": tick, "duration_ticks": duration}
        if slide_from is not None and slide_from != pitch and low <= slide_from <= high:
            update |= {"slide_from": slide_from, "slide_ms": max(1, min(600, slide_ms))}
        shaped.append(note.model_copy(update=update))
        line_index = len(shaped) - 1
    return tuple(shaped)
