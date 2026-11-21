"""Translate temporary chorus cues into settings and short lead replies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.music import CHORUS_BARS, PPQ, TRACKS, Note, validate_notes

if TYPE_CHECKING:
    from band.music import BarPlan, ImprovisationPhrase, ImprovisationPlan, MusicSettings

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


def apply_improvised_fill(
    plan: BarPlan,
    settings: MusicSettings,
    phrase: ImprovisationPhrase,
) -> BarPlan:
    """Give the lead an answer in an available rhythmic gap."""
    if (
        phrase.character not in {"answer", "build"}
        or plan.transition
        or phrase.lead in plan.fallback_tracks
    ):
        return plan
    player = plan.player_response.phrase if plan.player_response else None
    if player and (
        player.activity == "busy"
        or (phrase.lead == "piano" and player.pitch_register == "high")
        or (phrase.lead == "bass" and player.pitch_register == "low")
    ):
        return plan

    track = TRACKS[phrase.lead]
    part = tuple(note for note in plan.notes if note.track_id == phrase.lead)
    if len(part) >= track.max_notes:
        return plan
    conductor = settings.conductor
    groove = conductor.profile.groove if conductor.profile else conductor.style
    pulse = settings.pulse_ticks
    if settings.meter == "6/8":
        candidates = (pulse + PPQ, pulse + PPQ // 2, PPQ)
    elif settings.meter == "3/4":
        candidates = (2 * pulse + PPQ // 2, settings.bar_ticks - PPQ // 8, pulse + PPQ // 2)
    else:
        offbeat = PPQ * 2 // 3 if groove in {"swing", "shuffle"} else PPQ // 2
        candidates = (3 * PPQ + offbeat, settings.bar_ticks - PPQ // 8, PPQ + offbeat)
    for tick in candidates:
        if player and player.accent_tick is not None and abs(tick - player.accent_tick) < PPQ // 3:
            continue
        duration = 100 if phrase.lead == "drums" else PPQ // 6
        duration = min(duration, settings.bar_ticks - tick)
        if any(
            note.start_tick < tick + duration and note.start_tick + note.duration_ticks > tick
            for note in part
        ):
            continue
        if phrase.lead == "drums":
            pitch = 45 if phrase.character == "build" else 37
        else:
            chord = settings.chord_at(plan.bar)
            degree = chord.intervals[1 if plan.bar % 2 else min(3, len(chord.intervals) - 1)]
            target = 45 if phrase.lead == "bass" else 76
            pitch = min(
                (
                    p
                    for p in range(track.min_pitch, track.max_pitch + 1)
                    if (p - chord.root - degree) % 12 == 0
                ),
                key=lambda p: abs(p - target),
            )
        velocity = (73 if phrase.character == "build" else 63) * (
            0.5 + settings.conductor.intensity / 110
        )
        reply = Note(
            event_id=f"improv-{phrase.lead}-{plan.bar}-{tick}-{pitch}",
            track_id=phrase.lead,
            bar=plan.bar,
            start_tick=tick,
            duration_ticks=duration,
            pitch=pitch,
            velocity=round(velocity),
            channel=track.channel,
        )
        return plan.model_copy(update={"notes": validate_notes(plan.bar, (*plan.notes, reply))})
    return plan
