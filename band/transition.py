"""Write pickup and arrival notes while leaving the tempo clock to the scheduler."""

from band.music import (
    PPQ,
    TRACK_DEFINITIONS,
    BarPlan,
    Chord,
    GrooveTransition,
    MusicSettings,
    Note,
    TrackId,
    parse_chord,
    validate_notes,
)

# Each figure points toward the incoming style, measured from beat four.
_PICKUP_FIGURES = {
    "swing": ((0, 51, 70), (PPQ * 2 // 3, 51, 57)),
    "bossa": ((0, 37, 71), (PPQ // 2, 37, 63)),
    "funk": ((0, 38, 79), (PPQ // 2, 42, 60), (PPQ * 3 // 4, 45, 72)),
    "ballad": ((0, 42, 54), (PPQ // 2, 37, 53)),
}
_GROOVE_PICKUPS = {
    "swing": _PICKUP_FIGURES["swing"],
    "bossa": _PICKUP_FIGURES["bossa"],
    "straight_eighth": ((0, 38, 79), (PPQ // 2, 47, 72)),
    "sixteenth": _PICKUP_FIGURES["funk"],
    "shuffle": ((0, 38, 77), (PPQ * 2 // 3, 45, 65)),
    "one_drop": ((0, 42, 52), (PPQ // 2, 37, 65)),
    "four_on_floor": ((0, 36, 82), (PPQ // 2, 46, 65)),
    "half_time": _PICKUP_FIGURES["ballad"],
}
# Match each style's usual attack and leave longer note tails to its groove.
_ARRIVALS = {
    "swing": (51, 81, 65, 4 * PPQ // 5),
    "bossa": (42, 73, 62, 7 * PPQ // 10),
    "funk": (42, 87, 67, PPQ // 2),
    "ballad": (42, 63, 54, 9 * PPQ // 10),
}


def _note(
    track: TrackId,
    bar: int,
    tick: int,
    duration: int,
    pitch: int,
    velocity: int,
    settings: MusicSettings,
) -> Note:
    strength = 0.5 + settings.conductor.intensity / 110
    return Note(
        event_id=f"transition:{track}:{bar}:{tick}:{pitch}",
        track_id=track,
        bar=bar,
        start_tick=tick,
        duration_ticks=duration,
        pitch=pitch,
        velocity=max(1, min(127, round(strength * velocity))),
        channel=TRACK_DEFINITIONS[track].channel,
    )


def _pitches(pitch_classes: set[int], low: int, high: int) -> tuple[int, ...]:
    return tuple(pitch for pitch in range(low, high + 1) if pitch % 12 in pitch_classes)


def _bass_root(chord: Chord, reference: int) -> int:
    return min(_pitches({chord.bass}, 32, 54), key=lambda pitch: abs(pitch - reference))


def _pickup_figure(
    transition: GrooveTransition,
    settings: MusicSettings,
) -> tuple[tuple[int, int, int], ...]:
    old_bpm, new_bpm = transition.from_bpm, transition.to_bpm
    if transition.from_meter != transition.to_meter:
        # Articulate subdivisions in the outgoing pulse and leave the new accents
        # to the first bar of the incoming arrangement.
        start = settings.bar_ticks - settings.pulse_ticks
        if transition.to_meter == "6/8":
            step = settings.pulse_ticks // 3
            return ((start, 37, 69), (start + step, 45, 62), (start + 2 * step, 47, 75))
        step = settings.pulse_ticks // (3 if settings.meter == "6/8" else 2)
        return ((start, 37, 69), (settings.bar_ticks - step, 47, 75))
    if old_bpm * 3 == new_bpm * 2 or old_bpm * 2 == new_bpm * 3:
        # The new pulse equals either a triplet or a dotted pulse in the old rhythm.
        # The arrival is the third stroke.
        spacing = settings.pulse_ticks * old_bpm // new_bpm
        pivot_pitch, velocity = (37, 69) if transition.to_style in {"bossa", "ballad"} else (38, 77)
        return (
            (settings.bar_ticks - 2 * spacing, pivot_pitch, velocity),
            (settings.bar_ticks - spacing, pivot_pitch, velocity),
        )
    figure = (
        _GROOVE_PICKUPS[transition.to_groove]
        if transition.to_groove
        else _PICKUP_FIGURES[transition.to_style]
    )
    last = settings.bar_ticks - settings.pulse_ticks
    if settings.meter == "6/8":
        subdivision = PPQ // 2
        return tuple(
            (last + index * subdivision, pitch, velocity)
            for index, (_, pitch, velocity) in enumerate(figure)
        )
    return tuple(
        (last + offset * settings.pulse_ticks // PPQ, pitch, velocity)
        for offset, pitch, velocity in figure
    )


def _pickup(
    plan: BarPlan,
    settings: MusicSettings,
    transition: GrooveTransition,
    next_chord: Chord,
) -> tuple[Note, ...]:
    figure = _pickup_figure(transition, settings)
    cutoff, last_tick = figure[0][0], figure[-1][0]
    placed = [
        _note("drums", plan.bar, tick, 90, pitch, velocity, settings)
        for tick, pitch, velocity in figure
    ]

    previous_bass = next(
        (
            note.pitch
            for note in reversed(plan.notes)
            if note.track_id == "bass" and note.start_tick < cutoff
        ),
        42,
    )
    chord = settings.chord_at(plan.bar)
    current_pitches = {(chord.root + interval) % 12 for interval in chord.intervals}
    current_pitches.add(chord.bass)
    target = _bass_root(next_chord, previous_bass)
    approach = min(
        _pitches(current_pitches, 32, 54),
        key=lambda pitch: (abs(pitch - target), abs(pitch - previous_bass)),
    )
    placed.append(
        _note(
            "bass",
            plan.bar,
            last_tick,
            min(PPQ // 3, settings.bar_ticks - last_tick - 1),
            approach,
            73,
            settings,
        )
    )

    counts = {track: sum(note.track_id == track for note in placed) for track in TRACK_DEFINITIONS}
    kept = []
    # The old groove remains intact until the cue, including shortened chord tails.
    for note in plan.notes:
        if (
            note.start_tick >= cutoff
            or counts[note.track_id] >= TRACK_DEFINITIONS[note.track_id].max_notes
        ):
            continue
        duration = min(note.duration_ticks, cutoff - note.start_tick)
        kept.append(
            note
            if duration == note.duration_ticks
            else note.model_copy(update={"duration_ticks": duration})
        )
        counts[note.track_id] += 1
    return (*kept, *placed)


def _arrival(
    plan: BarPlan,
    settings: MusicSettings,
    transition: GrooveTransition,
) -> tuple[Note, ...]:
    if (
        transition.to_groove is not None
        or transition.to_meter != "4/4"
        or any(note.event_id.endswith("-motif") for note in plan.notes)
    ):
        # Let the new groove establish its own entrance. One-drop, for example,
        # needs the space left by its silent kick on the first beat.
        return plan.notes
    chord = settings.chord_at(plan.bar)
    bass_reference = next((note.pitch for note in plan.notes if note.track_id == "bass"), 42)
    opening_bass = _bass_root(chord, bass_reference)
    cymbal, kick_velocity, cymbal_velocity, bass_gate = _ARRIVALS[transition.to_style]
    bass_next = next(
        (note.start_tick for note in plan.notes if note.track_id == "bass" and note.start_tick > 0),
        settings.bar_ticks,
    )
    downbeat = [
        _note("drums", plan.bar, 0, 90, 36, kick_velocity, settings),
        _note("drums", plan.bar, 0, 90, cymbal, cymbal_velocity, settings),
        _note("bass", plan.bar, 0, min(bass_gate, bass_next), opening_bass, 82, settings),
    ]

    # A guide-tone shell identifies the arriving chord and leaves register for the bass.
    guide_classes = {
        (chord.root + chord.intervals[1]) % 12,
        (chord.root + chord.intervals[min(3, len(chord.intervals) - 1)]) % 12,
    }
    opening_piano = [
        note
        for note in plan.notes
        if note.track_id == "piano" and note.start_tick == 0 and note.pitch % 12 in guide_classes
    ]
    if opening_piano:
        downbeat.extend(opening_piano)
    else:
        piano_gate = PPQ // 3 if transition.to_style == "funk" else PPQ // 2
        for pitch_class in sorted(guide_classes):
            pitch = min(
                _pitches({pitch_class}, max(55, opening_bass + 5), 76),
                key=lambda note: abs(note - 63),
            )
            downbeat.append(_note("piano", plan.bar, 0, piano_gate, pitch, 68, settings))

    accents = {(note.track_id, note.pitch): note.duration_ticks for note in downbeat}
    counts = {
        track: sum(note.track_id == track for note in downbeat) for track in TRACK_DEFINITIONS
    }
    for note in plan.notes:
        # After the ensemble's first-beat entrance, keep the new groove's phrasing.
        if note.start_tick == 0 or (note.track_id == "piano" and note.start_tick < PPQ):
            continue
        if note.start_tick < accents.get((note.track_id, note.pitch), 0):
            continue
        if counts[note.track_id] < TRACK_DEFINITIONS[note.track_id].max_notes:
            downbeat.append(note)
            counts[note.track_id] += 1
    return tuple(downbeat)


def apply_transition(
    plan: BarPlan,
    settings: MusicSettings,
    transition: GrooveTransition,
    *,
    next_chord: Chord | None = None,
) -> BarPlan:
    """Return validated transition notes, preserving the source plan and its protocol metadata."""
    if transition.phase == "pickup":
        target = next_chord
        if target is None:
            target = (
                parse_chord(transition.next_chord)
                if transition.next_chord
                else settings.chord_at(plan.bar + 1)
            )
        notes = _pickup(plan, settings, transition, target)
    else:
        notes = _arrival(plan, settings, transition)
    return plan.model_copy(
        update={
            "notes": validate_notes(plan.bar, notes),
            "transition": transition,
        }
    )
