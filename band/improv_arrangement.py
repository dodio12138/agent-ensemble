"""Translate temporary chorus cues into settings and the lead's improvised lines."""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.music import CHORUS_BARS, PPQ, TRACK_DEFINITIONS, TRACKS, Note, validate_notes

if TYPE_CHECKING:
    from band.music import (
        BarPlan,
        Chord,
        ImprovisationPhrase,
        ImprovisationPlan,
        MusicSettings,
        TrackId,
    )

_CHARACTER = {
    "space": (-0.24, -11),
    "answer": (0.02, 0),
    "build": (0.22, 12),
    "resolve": (-0.15, -8),
}
_SUPPORT = {
    "drums": {"drums": "driving", "bass": "root", "piano": "sustained"},
    "bass": {"drums": "sparse", "bass": "walking", "piano": "sustained"},
    "piano": {"drums": "sparse", "bass": "root", "piano": "comp"},
}


def phrase_for_bar(
    plan: ImprovisationPlan,
    bar: int,
    start_bar: int,
) -> ImprovisationPhrase | None:
    """Select the two-bar cue inside this chorus."""
    offset = bar - start_bar
    return plan.phrases[offset // 2] if 0 <= offset < CHORUS_BARS else None


def bind_improvisation(
    plan: ImprovisationPlan, settings: MusicSettings, start_bar: int, end_bar: int
) -> ImprovisationPlan:
    if plan.relative_to_start or not plan.harmonic_moves:
        return plan
    first = start_bar % CHORUS_BARS
    # Establish the written chord on entry. The two scored edits are optional.
    progression = list(settings.progression[first:] + settings.progression[:first])
    playable = end_bar - start_bar
    movable = playable if settings.loop else playable - 1
    moves = sorted(
        (
            move.model_copy(update={"position": offset})
            for move in plan.harmonic_moves
            if 0 < (offset := (move.position - first) % CHORUS_BARS) < movable
        ),
        key=lambda move: move.position,
    )
    for move in moves:
        progression[move.position] = move.chord
    return plan.model_copy(
        update={
            "progression": tuple(progression),
            "harmonic_moves": tuple(moves),
            "relative_to_start": True,
        }
    )


def project_improvisation(
    settings: MusicSettings,
    bar: int,
    start_bar: int,
    plan: ImprovisationPlan,
    variant: int,
) -> MusicSettings:
    """Keep the groove and theme index while giving each phrase a different lead."""
    phrase = phrase_for_bar(plan, bar, start_bar)
    if phrase is None:
        return settings

    density_shift, intensity_shift = _CHARACTER[phrase.character]
    support = _SUPPORT[phrase.lead].copy()
    if phrase.character == "answer" and phrase.lead in {"bass", "piano"}:
        support[phrase.lead] = "syncopated"
    elif phrase.character == "resolve":
        support.update(
            drums="balanced" if phrase.lead == "drums" else "sparse",
            bass="walking" if phrase.lead == "bass" else "root",
            piano="sustained",
        )
    elif phrase.character == "space":
        support["drums"] = "balanced" if phrase.lead == "drums" else "sparse"
    intensity = max(0, min(100, settings.conductor.intensity + intensity_shift))
    density = max(0.0, min(1.0, settings.density + density_shift))
    seed = (settings.seed + 7919 * variant) % (2**53 - 1)
    progression = plan.progression
    if plan.relative_to_start and (first := start_bar % CHORUS_BARS):
        progression = progression[-first:] + progression[:-first]
    profile = settings.conductor.profile
    if profile is not None:
        support = {
            "drums": "driving" if phrase.lead == "drums" else "sparse",
            "bass": "syncopated" if phrase.lead == "bass" else settings.conductor.bass,
            "piano": settings.conductor.piano,
        }
    return settings.model_copy(
        update={
            "progression": progression,
            "density": density,
            "seed": seed,
            "conductor": settings.conductor.model_copy(update={**support, "intensity": intensity}),
        }
    )


# Two-bar lines for each cue character, on a sixteenth grid:
# (start, length, chord-relative target, velocity). "app" approaches the next chord's
# third from a semitone below; "b3" is a blue-note grace into the third.
_LINES: dict[str, tuple[tuple[tuple[int, int, str, int], ...], ...]] = {
    "space": (
        ((4, 6, "5", 70), (12, 4, "3", 66)),
        ((0, 8, "9", 72), (10, 4, "7", 64)),
    ),
    "answer": (
        ((2, 2, "5", 76), (4, 2, "3", 74), (6, 2, "R", 72), (8, 6, "5", 80)),
        ((0, 2, "7", 78), (2, 2, "9", 80), (4, 4, "7", 76), (10, 2, "5", 72), (12, 4, "3", 76)),
    ),
    "build": (
        (
            (0, 2, "3", 78),
            (2, 2, "5", 80),
            (4, 2, "7", 82),
            (6, 2, "9", 84),
            (8, 3, "5", 84),
            (11, 3, "7", 86),
            (14, 2, "9", 88),
        ),
        (
            (0, 1, "b3", 88),
            (1, 1, "3", 90),
            (2, 2, "5", 92),
            (4, 2, "7", 94),
            (6, 2, "9", 96),
            (8, 2, "R+", 100),
            (10, 6, "9", 98),
        ),
    ),
    "resolve": (
        ((0, 4, "9", 80), (4, 2, "7", 76), (6, 2, "5", 74), (8, 4, "3", 76), (14, 2, "app", 70)),
        ((0, 10, "R", 78),),
    ),
}
_ORDER = {"R": 0, "b3": 1.5, "3": 2, "5": 3, "7": 4, "9": 5, "R+": 6}
# Melodic register and centre for whoever plays the line.
_REGISTER = {
    "piano": (64, 84, 72),
    "lead_guitar": (57, 81, 67),
    "synth": (62, 84, 72),
    "melody": (64, 88, 76),
    "bass": (40, 55, 47),
}
_TOM_FILL = ((12, 50, 84), (13, 48, 82), (14, 47, 86), (15, 45, 90))


def _offset(token: str, chord: Chord) -> int:
    intervals = chord.intervals
    third = intervals[1]
    upper = intervals[3] if len(intervals) > 3 else (10 if third == 3 else 9)
    return {
        "R": 0,
        "R+": 12,
        "b3": 3,
        "3": third,
        "5": intervals[2],
        "7": upper,
        "9": 14,
    }[token]


def _tick(step: int, swung: bool) -> int:
    beat, sub = divmod(step, 4)
    if not swung:
        return beat * PPQ + sub * PPQ // 4
    return beat * PPQ + (0, PPQ // 3, PPQ * 2 // 3, PPQ * 5 // 6)[sub]


def improvised_line(
    settings: MusicSettings,
    bar: int,
    character: str,
    half: int,
    track: TrackId,
) -> tuple[Note, ...]:
    """A solo line for one bar of a two-bar cue, following the chord under it."""
    chord = settings.chord_at(bar)
    following = settings.chord_at(bar + 1)
    conductor = settings.conductor
    groove = conductor.profile.groove if conductor.profile else conductor.style
    swung = groove in {"swing", "shuffle"}
    low, high, centre = _REGISTER[track]
    definition = TRACK_DEFINITIONS[track]
    strength = 0.5 + conductor.intensity / 110
    cells = list(_LINES[character][half])
    if track == "bass" and cells[0][0] != 0:
        cells.insert(0, (0, 2, "R", 84))  # Keep the downbeat anchored under a bass solo.
    # Builds start low and climb across the cue; resolutions start high and fall.
    lift = {"build": (-4, 3), "resolve": (4, 0)}.get(character, (0, 0))[half]
    previous, last_rank = centre + lift, None
    notes = []
    for start, length, token, velocity in cells:
        tick = _tick(start, swung)
        if tick >= settings.bar_ticks:
            continue
        if token == "app":
            # A half step below where the next bar starts: the answer's first note when
            # it follows in this cue, otherwise the next chord's third.
            landing = _LINES[character][1][0][2] if half == 0 else "3"
            target = following.root + _offset(landing, following) - 1
            pitch_class = target % 12
            up = None
        else:
            pitch_class = (chord.root + _offset(token, chord)) % 12
            up = None if last_rank is None else _ORDER[token] > last_rank
            last_rank = _ORDER[token]
        candidates = [p for p in range(low, high + 1) if p % 12 == pitch_class]
        side = [p for p in candidates if up is None or (p > previous if up else p < previous)]
        pitch = min(side or candidates, key=lambda p: abs(p - previous))
        end = min(_tick(start + length, swung) - 20, settings.bar_ticks - 10)
        notes.append(
            Note(
                event_id=f"improv-{track}-{bar}-{len(notes)}-{pitch}",
                track_id=track,
                bar=bar,
                start_tick=tick,
                duration_ticks=max(30, end - tick),
                pitch=pitch,
                velocity=max(1, min(127, round(velocity * strength))),
                channel=definition.channel,
            )
        )
        previous = pitch
    return tuple(notes[: definition.max_notes])


def apply_improvised_fill(
    plan: BarPlan,
    settings: MusicSettings,
    phrase: ImprovisationPhrase,
    half: int | None = None,
) -> BarPlan:
    """Give the cue's lead a real line: a melodic solo, a bass solo, or a drum fill.

    ``half`` is the bar's place inside its two-bar cue (0 = call, 1 = answer).
    """
    if plan.transition or phrase.lead in plan.fallback_tracks:
        return plan
    player = plan.player_response.phrase if plan.player_response else None
    if player and (
        player.activity == "busy"
        or (phrase.lead == "piano" and player.pitch_register == "high")
        or (phrase.lead == "bass" and player.pitch_register == "low")
    ):
        return plan
    half = plan.bar % 2 if half is None else half % 2
    # In the full bands the lead voice takes melodic solos; keys keep comping.
    soloists: dict[str, TrackId] = {"modern": "lead_guitar", "game": "melody"}
    track: TrackId = (
        soloists.get(settings.lineup, "piano") if phrase.lead == "piano" else phrase.lead
    )
    return apply_solo(plan, settings, track, phrase.character, half)


def apply_solo(
    plan: BarPlan,
    settings: MusicSettings,
    track: TrackId,
    character: str,
    half: int,
) -> BarPlan:
    """Replace one track's bar with a solo line, or give the drums a fill."""
    if track == "drums":
        if half == 0 and character != "build":
            return plan
        fill_from = 3 * PPQ
        if settings.meter != "4/4" or fill_from >= settings.bar_ticks:
            return plan
        kept = tuple(
            note for note in plan.notes if note.track_id != "drums" or note.start_tick < fill_from
        )
        strength = 0.5 + settings.conductor.intensity / 110
        fill = tuple(
            Note(
                event_id=f"improv-drums-{plan.bar}-{step}-{pitch}",
                track_id="drums",
                bar=plan.bar,
                start_tick=step * PPQ // 4,
                duration_ticks=90,
                pitch=pitch,
                velocity=max(1, min(127, round(velocity * strength))),
                channel=TRACKS["drums"].channel,
            )
            for step, pitch, velocity in _TOM_FILL
        )
        notes = kept + fill
    else:
        line = improvised_line(settings, plan.bar, character, half, track)
        if not line:
            return plan
        kept = tuple(note for note in plan.notes if note.track_id != track)
        if track == "piano":
            # Under a piano solo the left hand keeps a soft shell on the downbeat.
            chord = settings.chord_at(plan.bar)
            shell = sorted(
                {
                    min(
                        (p for p in range(48, 60) if (p - chord.root - tone) % 12 == 0),
                        key=lambda p: abs(p - 54),
                    )
                    for tone in (chord.intervals[1], chord.intervals[-1])
                }
            )
            line += tuple(
                Note(
                    event_id=f"improv-piano-{plan.bar}-shell-{pitch}",
                    track_id="piano",
                    bar=plan.bar,
                    start_tick=0,
                    duration_ticks=min(settings.bar_ticks // 2, 2 * PPQ) - 20,
                    pitch=pitch,
                    velocity=52,
                    channel=TRACKS["piano"].channel,
                )
                for pitch in shell
            )
        notes = kept + line
    try:
        return plan.model_copy(update={"notes": validate_notes(plan.bar, notes)})
    except ValueError:
        return plan
