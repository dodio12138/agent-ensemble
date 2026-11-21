"""Rule-based musicians and per-track planning deadlines."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import product
from typing import TYPE_CHECKING, Protocol

from .music import (
    BAR_TICKS,
    BEATS_PER_BAR,
    CHORUS_BARS,
    MELODY_STEPS_PER_QUARTER,
    PPQ,
    TRACK_DEFINITIONS,
    TRACKS,
    AgentDecision,
    Articulation,
    BarPlan,
    Chord,
    Groove,
    MusicSettings,
    NegotiationEvent,
    Note,
    PlayerPhrase,
    PlayerResponse,
    Style,
    TrackId,
    active_tracks,
    parse_chord,
    validate_notes,
)
from .negotiation import Negotiator, Participant

if TYPE_CHECKING:
    from .music import SongSection

SWUNG_EIGHTH = PPQ * 2 // 3
QUARTERS = tuple(beat * PPQ for beat in range(BEATS_PER_BAR))
BACKBEATS = QUARTERS[1::2]
EIGHTH = PPQ // 2
SIXTEENTH = PPQ // 4
OFFBEATS = tuple(tick + EIGHTH for tick in QUARTERS)


@dataclass(frozen=True, slots=True)
class BarContext:
    bar: int
    settings: MusicSettings
    chord: Chord
    kicks: tuple[int, ...] = ()
    drum_notes: int = 0
    bass_notes: int = 0
    bass_high: int = 43
    player_phrase: PlayerPhrase | None = None
    voice_leading: bool = False
    prior_chord: Chord | None = None
    next_chord: Chord | None = None
    piano_onsets: tuple[int, ...] = ()
    piano_pitches: frozenset[int] = frozenset()
    section: SongSection | None = None
    section_bar: int = 0

    def random_for(self, track: TrackId) -> random.Random:
        return random.Random(f"{self.settings.seed}:{self.bar}:{track}")

    def observe(self, track: TrackId, notes: tuple[Note, ...]) -> BarContext:
        if track == "drums":
            return replace(
                self,
                kicks=tuple(note.start_tick for note in notes if note.pitch == 36),
                drum_notes=len(notes),
            )
        if track == "bass":
            return replace(
                self,
                bass_notes=len(notes),
                bass_high=max((note.pitch for note in notes), default=43),
            )
        if track == "piano" and self.settings.lineup == "modern":
            return replace(
                self,
                piano_onsets=tuple(sorted({note.start_tick for note in notes})),
                piano_pitches=frozenset(note.pitch for note in notes),
            )
        return self


@dataclass(frozen=True, slots=True)
class AgentOutput:
    intent: str
    notes: tuple[Note, ...]
    player_intent: str | None = None


class InstrumentAgent(Protocol):
    async def generate(self, context: BarContext) -> AgentOutput: ...


def _note(
    track: TrackId,
    context: BarContext,
    tick: int,
    duration: int,
    pitch: int,
    velocity: int,
    *,
    articulation: Articulation = "normal",
) -> Note:
    bar = context.bar
    strength = 0.5 + context.settings.conductor.intensity / 110
    return Note(
        event_id=f"{track}-{bar}-{tick}-{pitch}",
        track_id=track,
        bar=bar,
        start_tick=tick,
        duration_ticks=min(duration, context.settings.bar_ticks - tick),
        pitch=pitch,
        velocity=max(1, min(127, round(velocity * strength))),
        channel=TRACK_DEFINITIONS[track].channel,
        articulation=articulation,
    )


def _in_register(pitch_class: int, low: int, high: int, target: int) -> int:
    first = low + (pitch_class - low) % 12
    return min(range(first, high + 1, 12), key=lambda pitch: abs(pitch - target))


def _bass_approach(chord: Chord, next_chord: Chord, low: int, high: int, anchor: int) -> int:
    destination = _in_register(next_chord.bass, low, high, anchor)
    allowed = {chord.bass, *((chord.root + step) % 12 for step in chord.intervals)}
    return min(
        (pitch for pitch in range(low, high + 1) if pitch % 12 in allowed),
        key=lambda pitch: (abs(pitch - destination), abs(pitch - anchor)),
    )


_MOTIF_CONTOURS = {
    "rise": ((-4, 0, 5), (1, 4, 8)),
    "fall": ((6, 2, -3), (4, 0, -6)),
    "arch": ((-3, 4, 8), (7, 3, -4)),
    "repeat": ((0, 0, 4), (0, 0, -2)),
}


def _song_motif(track: TrackId, context: BarContext) -> tuple[Note, ...]:
    """Recur in two-bar call/response pairs, adjusting targets to the current harmony."""
    section, phrase = context.section, context.player_phrase
    if section is None or (phrase and phrase.activity == "busy"):
        return ()
    settings = context.settings
    position = context.section_bar
    if section.lead and (bar := section.lead[position]) is not None:
        step = PPQ // MELODY_STEPS_PER_QUARTER
        return tuple(
            _note(track, context, start * step, duration * step, pitch, velocity)
            for start, duration, pitch, velocity in bar
            if not phrase
            or phrase.accent_tick is None
            or abs(start * step - phrase.accent_tick) >= PPQ // 3
        )
    answer = position % 2 == 1
    closing = position == section.bars - 1
    subdivision = EIGHTH if settings.meter == "6/8" else _offbeat(_rhythm(context))
    pulse = settings.pulse_ticks
    late_answer = max(settings.bar_ticks - pulse, pulse + subdivision)
    if closing:
        # The last phrase lands early enough to leave the handoff audible.
        starts = (0, pulse) if section.role == "outro" else (subdivision, pulse)
    elif section.texture == "sparse":
        starts = (pulse, late_answer) if answer else (0, pulse + subdivision)
    elif section.texture == "answer":
        starts = (pulse, late_answer) if answer else (0, subdivision, pulse)
    else:
        starts = (
            (0, pulse + subdivision, settings.bar_ticks - EIGHTH)
            if answer
            else (
                0,
                subdivision,
                pulse + subdivision,
            )
        )

    low = 74 if track == "piano" else 67
    if phrase and phrase.pitch_register == "high":
        low = 65
    high = TRACK_DEFINITIONS[track].max_pitch
    opening = parse_chord(section.progression[0])
    center = 82 if section.role == "b" else 76
    if phrase and phrase.pitch_register == "high":
        center = 70
    anchor = _in_register(opening.root, low, high, max(low, center))
    chord = context.chord
    tones = {(chord.root + interval) % 12 for interval in chord.intervals}
    available = tuple(pitch for pitch in range(low, high + 1) if pitch % 12 in tones)
    contour = _MOTIF_CONTOURS[section.motif][answer]
    notes = []
    prior_pitch = anchor
    for index, start in enumerate(starts):
        if phrase and phrase.accent_tick is not None and abs(start - phrase.accent_tick) < PPQ // 3:
            continue
        relative = contour[index] if len(starts) == 3 else contour[2 * index]
        target = anchor + relative
        if closing and index == len(starts) - 1:
            pitch = _in_register(chord.root, low, high, prior_pitch)
        else:
            pitch = min(
                available,
                key=lambda note: (
                    3 * abs(note - target) + max(0, abs(note - prior_pitch) - 5),
                    note,
                ),
            )
        next_start = starts[index + 1] if index + 1 < len(starts) else settings.bar_ticks
        duration = pulse * 3 // 4 if closing and index == len(starts) - 1 else PPQ // 3
        velocity = (76 if index == 0 else 69) + (4 if section.role == "b" else 0)
        if phrase:
            velocity -= 13
        note = _note(track, context, start, min(duration, next_start - start), pitch, velocity)
        notes.append(note.model_copy(update={"event_id": f"{note.event_id}-motif"}))
        prior_pitch = pitch
    return tuple(notes)


def _bass_pulses(context: BarContext, grid: set[int]) -> tuple[int, ...]:
    for kick in context.kicks:
        if len(grid) >= TRACKS["bass"].max_notes:
            break
        if all(abs(kick - tick) >= PPQ // 4 for tick in grid):
            grid.add(kick)
    return tuple(sorted(grid))


def _answer_tick(phrase: PlayerPhrase, offbeat: int, settings: MusicSettings) -> int | None:
    if phrase.accent_tick is None:
        return None
    pulse = settings.pulse_ticks
    beat, offset = divmod(phrase.accent_tick, pulse)
    subdivisions = (EIGHTH, 2 * EIGHTH, pulse) if settings.meter == "6/8" else (offbeat, pulse)
    tick = beat * pulse + next(value for value in subdivisions if value > offset)
    return tick if tick < settings.bar_ticks else None


DRUM_GROOVES = {
    "swing": ((0, 36, 80), (1920, 36, 70), (960, 44, 58), (2880, 44, 58)),
    "bossa": (
        (0, 36, 73),
        (1440, 36, 67),
        (1920, 36, 70),
        (3360, 36, 64),
        (480, 37, 58),
        (1440, 37, 60),
        (2400, 37, 57),
        (2880, 37, 61),
    ),
    "funk": (
        (0, 36, 87),
        (720, 36, 72),
        (1920, 36, 80),
        (2640, 36, 77),
        (3360, 36, 70),
        (960, 38, 86),
        (2880, 38, 88),
    ),
    "ballad": ((0, 36, 61), (1920, 36, 56), (1920, 37, 51)),
}
DRUM_TIMEKEEPING = {
    "swing": (51, QUARTERS),
    "bossa": (42, QUARTERS),
    "funk": (42, QUARTERS),
    "ballad": (42, QUARTERS[::2]),
}
DRUM_INTENT = {
    "swing": "摇摆镲与反拍军鼓",
    "bossa": "巴萨诺瓦的边击与切分底鼓",
    "funk": "十六分切分底鼓与重反拍",
    "ballad": "轻踩镲与疏落底鼓",
}
BASS_SYNCOPATION = {
    "swing": (0, PPQ + SWUNG_EIGHTH, 2 * PPQ, 3 * PPQ + SWUNG_EIGHTH),
    "bossa": (0, 3 * PPQ // 2, 2 * PPQ, 7 * PPQ // 2),
    "funk": (0, 3 * PPQ // 4, 2 * PPQ, 11 * PPQ // 4, 7 * PPQ // 2),
    "ballad": (0, 3 * PPQ // 2, 2 * PPQ),
}
BASS_INTENT = {
    "walking": "跟随底鼓并用行走低音连接和弦",
    "root": "用根音与五音留出空间",
    "syncopated": "与底鼓交错的切分低音",
}
PIANO_COMP = {
    "swing": ((SWUNG_EIGHTH, PPQ), (2 * PPQ + SWUNG_EIGHTH, 2 * PPQ // 3)),
    "bossa": ((PPQ // 2, PPQ // 2), (3 * PPQ // 2, PPQ // 2), (3 * PPQ, PPQ)),
    "funk": ((PPQ // 2, PPQ // 4), (7 * PPQ // 4, PPQ // 4), (11 * PPQ // 4, PPQ // 4)),
    "ballad": ((0, PPQ * 2), (2 * PPQ + PPQ // 2, PPQ)),
}
PIANO_INTENT = {
    "comp": "用导音和弦填补鼓与低音的间隙",
    "sustained": "长音和弦留出旋律空间",
    "syncopated": "短促的切分和弦",
}


@dataclass(frozen=True, slots=True)
class RhythmFigure:
    """The kick, backbeat, cymbal grid, and optional driving accents for a groove."""

    kicks: tuple[int, ...]
    snares: tuple[int, ...]
    cymbal: int
    timekeeping: tuple[int, ...]
    accents: tuple[tuple[int, int, int], ...]
    intent: str


RHYTHM_FIGURES: dict[Groove, RhythmFigure] = {
    "straight_eighth": RhythmFigure(
        (0, 2 * PPQ, 2 * PPQ + EIGHTH),
        BACKBEATS,
        42,
        tuple(range(0, BAR_TICKS, EIGHTH)),
        ((PPQ + EIGHTH, 36, 72), (3 * PPQ + EIGHTH, 46, 64)),
        "直八分踩镲，第二和第四拍打军鼓反拍",
    ),
    "sixteenth": RhythmFigure(
        (0, 3 * SIXTEENTH, 2 * PPQ, 2 * PPQ + 3 * SIXTEENTH),
        BACKBEATS,
        42,
        tuple(range(0, BAR_TICKS, EIGHTH)),
        ((PPQ + 3 * SIXTEENTH, 38, 36), (3 * PPQ + 3 * SIXTEENTH, 36, 68)),
        "十六分切分底鼓，反拍之间穿插军鼓幽灵音",
    ),
    "shuffle": RhythmFigure(
        (0, 2 * PPQ),
        BACKBEATS,
        51,
        tuple(tick + offset for tick in QUARTERS for offset in (0, SWUNG_EIGHTH)),
        ((2 * PPQ + SWUNG_EIGHTH, 36, 70), (3 * PPQ + PPQ // 3, 38, 49)),
        "三连音的长短八分与二、四拍军鼓",
    ),
    "one_drop": RhythmFigure(
        (2 * PPQ,),
        (2 * PPQ,),
        42,
        OFFBEATS,
        ((PPQ + EIGHTH, 46, 59),),
        "第一拍留空，第三拍合击底鼓和边击",
    ),
    "four_on_floor": RhythmFigure(
        QUARTERS,
        BACKBEATS,
        46,
        OFFBEATS,
        ((0, 42, 55), (2 * PPQ, 42, 55)),
        "每拍底鼓，八分反拍开镲",
    ),
    "half_time": RhythmFigure(
        (0, PPQ + EIGHTH, 3 * PPQ),
        (2 * PPQ,),
        42,
        QUARTERS,
        ((3 * PPQ + EIGHTH, 36, 73), (PPQ + EIGHTH, 46, 60)),
        "军鼓落在第三拍，保留四拍速度的半拍律动",
    ),
}

# Each grid is in bar-relative ticks. Short piano durations leave room for the next attack.
PIANO_FIGURES: dict[Groove, tuple[tuple[int, int], ...]] = {
    "straight_eighth": ((0, PPQ), (PPQ + EIGHTH, EIGHTH), (2 * PPQ, PPQ + EIGHTH)),
    "sixteenth": ((EIGHTH, SIXTEENTH), (PPQ + 3 * SIXTEENTH, SIXTEENTH), (3 * PPQ, EIGHTH)),
    "shuffle": ((PPQ, EIGHTH), (3 * PPQ, EIGHTH), (3 * PPQ + SWUNG_EIGHTH, SIXTEENTH)),
    "one_drop": tuple((tick, SIXTEENTH) for tick in OFFBEATS),
    "four_on_floor": tuple((tick, SIXTEENTH) for tick in OFFBEATS),
    "half_time": ((0, PPQ + EIGHTH), (2 * PPQ + EIGHTH, PPQ)),
}
PIANO_FIGURE_INTENT: dict[Groove, str] = {
    "straight_eighth": "直八分切入简洁的三和弦",
    "sixteenth": "用短和弦补十六分切分",
    "shuffle": "用二、四拍与三连音尾拍回应",
    "one_drop": "在每拍后半拍短奏和弦",
    "four_on_floor": "在四拍底鼓间短奏反拍和弦",
    "half_time": "把和弦拉长，为第三拍军鼓留空",
}


type PianoShell = tuple[int, int]


def _piano_core(chord: Chord, open_fifth: bool) -> tuple[int, int]:
    if open_fifth:
        return 0, chord.intervals[2]
    return chord.intervals[1], chord.intervals[min(3, len(chord.intervals) - 1)]


def _shell_candidates(
    chord: Chord, low: int, high: int, open_fifth: bool
) -> tuple[PianoShell, ...]:
    first, second = ((chord.root + tone) % 12 for tone in _piano_core(chord, open_fifth))
    pitches = range(low, high + 1)
    return tuple(
        (min(left, right), max(left, right))
        for left, right in product(
            (note for note in pitches if note % 12 == first),
            (note for note in pitches if note % 12 == second),
        )
    )


def _register_cost(shell: PianoShell, center: int) -> int:
    return sum(abs(note - center) for note in shell)


def _voice_cost(previous: PianoShell, current: PianoShell) -> int:
    first, second = previous
    low, high = current
    nearest = min(
        abs(first - low) + abs(second - high),
        abs(first - high) + abs(second - low),
    )
    return 2 * nearest + 3 * (2 - len(set(previous).intersection(current)))


@lru_cache(maxsize=128)
def _shell_path(
    progression: tuple[str, ...],
    low: int,
    high: int,
    center: int,
    open_fifth: bool,
    loop: bool,
) -> tuple[PianoShell, ...]:
    """Choose close inversions over the cycle without depending on planning order."""
    candidates = tuple(
        _shell_candidates(parse_chord(chord), low, high, open_fifth) for chord in progression
    )
    first = min(candidates[0], key=lambda shell: (_register_cost(shell, center), shell))
    paths = {first: (0, (first,))}
    for choices in candidates[1:]:
        paths = {
            shell: min(
                (
                    cost + _voice_cost(before, shell) + _register_cost(shell, center) // 5,
                    (*path, shell),
                )
                for before, (cost, path) in paths.items()
            )
            for shell in choices
        }
    return min(
        (cost + (_voice_cost(last, first) if loop else 0), path)
        for last, (cost, path) in paths.items()
    )[1]


def _connected_piano(
    context: BarContext,
    guide_tones: list[int],
    low: int,
    high: int,
    center: int,
    open_fifth: bool,
) -> tuple[int, ...]:
    settings = context.settings
    path = _shell_path(settings.progression, low, high, center, open_fifth, settings.loop)
    index = context.bar % CHORUS_BARS
    shell = path[index]
    if context.prior_chord is not None and context.prior_chord != settings.chord_at(
        context.bar - 1
    ):
        previous = min(
            _shell_candidates(context.prior_chord, low, high, open_fifth),
            key=lambda candidate: (_register_cost(candidate, center), candidate),
        )
        shell = min(
            _shell_candidates(context.chord, low, high, open_fifth),
            key=lambda candidate: (
                _voice_cost(previous, candidate) + _voice_cost(candidate, shell) // 2,
                _register_cost(candidate, center),
                candidate,
            ),
        )
    pitches = list(shell)
    for tone in guide_tones[2:]:
        pitch_class = (context.chord.root + tone) % 12
        if not open_fifth and any(pitch % 12 == pitch_class for pitch in pitches):
            continue
        available = (
            pitch
            for pitch in range(low, high + 1)
            if pitch % 12 == pitch_class and pitch not in pitches
        )
        upper = min(
            available,
            key=lambda pitch: abs(pitch - max(shell[-1] + 1, center)),
            default=None,
        )
        if upper is not None:
            pitches.append(upper)
    return tuple(sorted(pitches))


ROCK_GENRES = frozenset({"rock", "hard_rock", "alternative_rock"})


def _rock_band(context: BarContext) -> bool:
    """A modern-lineup rock groove in 4/4, where guitars drive and keys sustain."""
    profile = context.settings.conductor.profile
    return (
        profile is not None
        and profile.genre in ROCK_GENRES
        and profile.groove in {"straight_eighth", "half_time"}
        and context.settings.meter == "4/4"
        and context.settings.lineup == "modern"
    )


def _rock_opened(context: BarContext) -> bool:
    """The chorus lift: guitars ring out and the drummer moves to the ride."""
    if context.section is not None:
        return context.section.texture == "full"
    return context.settings.density >= 0.7 and context.bar % CHORUS_BARS >= CHORUS_BARS // 2


def _rhythm(context: BarContext) -> Groove | Style:
    conductor = context.settings.conductor
    return conductor.profile.groove if conductor.profile else conductor.style


def _offbeat(groove: Groove | Style) -> int:
    return SWUNG_EIGHTH if groove in {"swing", "shuffle"} else EIGHTH


def _profile_drums(
    context: BarContext, groove: Groove, figure: RhythmFigure, rng: random.Random
) -> list[tuple[int, int, int]]:
    conductor = context.settings.conductor
    approach = conductor.drums
    density = context.settings.density
    rim = groove == "one_drop"
    hard_rock = conductor.profile is not None and conductor.profile.genre == "hard_rock"
    pattern = [(tick, 36, 91 if hard_rock else 81) for tick in figure.kicks]
    pattern.extend((tick, 37 if rim else 38, 92 if hard_rock else 74) for tick in figure.snares)
    keeps_offbeat = groove in {"one_drop", "four_on_floor"}
    for index, tick in enumerate(figure.timekeeping):
        essential = tick % PPQ == 0 or keeps_offbeat
        if essential or (approach != "sparse" and rng.random() < 0.4 + density * 0.6):
            pattern.append((tick, figure.cymbal, 59 if index % 2 == 0 else 48))
    if approach == "driving" and density >= 0.4:
        pattern.extend(figure.accents)
    if groove == "sixteenth" and approach != "sparse" and density >= 0.45:
        pattern.extend((tick + SIXTEENTH, figure.cymbal, 43) for tick in BACKBEATS)
    if hard_rock and groove in {"straight_eighth", "half_time"} and context.bar % 4 == 3:
        pattern.extend(((3 * PPQ + SIXTEENTH, 47, 76), (3 * PPQ + EIGHTH, 45, 79)))
    return pattern


def _meter_drums(
    settings: MusicSettings,
) -> tuple[list[tuple[int, int, int]], int, str]:
    approach = settings.conductor.drums
    if settings.meter == "3/4":
        pattern = [(0, 36, 81), (PPQ, 37, 61), (2 * PPQ, 37, 53)]
        pattern.extend((tick, 42, level) for tick, level in ((0, 62), (PPQ, 49), (2 * PPQ, 48)))
        if approach == "driving" and settings.density >= 0.5:
            pattern.extend(((EIGHTH, 42, 44), (2 * PPQ + EIGHTH, 45, 64)))
        return pattern, 42, "三拍华尔兹，第一拍底鼓，后二拍边击"
    pulse = settings.pulse_ticks
    pattern = [(0, 36, 82), (pulse, 38 if approach == "driving" else 37, 70)]
    accents = (65, 48, 53, 60, 47, 51)
    pattern.extend(
        (index * EIGHTH, 42, level)
        for index, level in enumerate(accents)
        if approach != "sparse" or settings.density >= 0.4 or index % 3 == 0
    )
    if approach == "driving" and settings.density >= 0.55:
        pattern.append((2 * EIGHTH, 36, 68))
    return pattern, 42, "六八拍两大拍，三枚八分成组"


def _meter_bass(
    settings: MusicSettings, fifth: int
) -> tuple[tuple[int, ...], tuple[int, ...], int, str]:
    pulse = settings.pulse_ticks
    approach = settings.conductor.bass
    busy = settings.density >= 0.45 and approach != "root"
    if settings.meter == "3/4":
        starts = (0, pulse, 2 * pulse) if busy else (0, 2 * pulse)
        return starts, (0, fifth, fifth), 7, "三拍低音就位，轻弹后拍和弦音"
    starts = (0, 2 * EIGHTH, pulse, pulse + 2 * EIGHTH) if busy else (0, pulse)
    return (
        starts,
        (0, fifth, 0, fifth) if busy else (0, fifth),
        8,
        "在六八拍两大拍落地，短音接向句尾",
    )


def _meter_piano(settings: MusicSettings) -> tuple[tuple[tuple[int, int], ...], str]:
    pulse = settings.pulse_ticks
    if settings.conductor.piano == "sustained":
        return ((0, settings.bar_ticks - EIGHTH // 2),), "长和弦托住本小节的主拍"
    if settings.meter == "3/4":
        return ((pulse, EIGHTH), (2 * pulse, EIGHTH)), "在后二拍轻弹短和弦，呼应首拍低音"
    if settings.conductor.piano == "syncopated":
        return ((EIGHTH, EIGHTH), (pulse + EIGHTH, EIGHTH)), "在两组八分的第二音补切分和弦"
    return ((0, 2 * EIGHTH), (pulse, 2 * EIGHTH)), "以两次和弦落点交代六八拍的大拍"


def _profile_bass(
    context: BarContext,
    groove: Groove,
    fifth: int,
) -> tuple[tuple[int, ...], tuple[int, ...], int, str]:
    conductor = context.settings.conductor
    approach = conductor.bass
    density = context.settings.density
    third = context.chord.intervals[1]
    last = context.chord.intervals[-1]
    if groove == "one_drop":
        starts, degrees = (EIGHTH, 2 * PPQ, 3 * PPQ + EIGHTH), (0, 0, fifth)
        return starts, degrees, 8, "用长短低音呼应第三拍，第一拍留白"
    if groove == "four_on_floor":
        starts = QUARTERS if density < 0.4 else tuple(range(0, BAR_TICKS, EIGHTH))
        return starts, (0, 12), 7, "在四拍底鼓上交替弹奏根音和八度"
    if groove == "shuffle":
        starts = QUARTERS if density < 0.45 else (*QUARTERS, 3 * PPQ + SWUNG_EIGHTH)
        degrees = (0, third, fifth, last) if approach == "walking" else (0, fifth)
        return starts, degrees, 8, "按长短八分切分，用和弦音走到下一小节"
    if groove == "sixteenth":
        starts = QUARTERS[::2] if approach == "root" else BASS_SYNCOPATION["funk"]
        return _bass_pulses(context, set(starts)), (0, fifth, 12, third), 6, "与切分底鼓交错"
    if groove == "half_time":
        starts = (0, 2 * PPQ) if approach == "root" else (0, PPQ + EIGHTH, 2 * PPQ, 3 * PPQ)
        return _bass_pulses(context, set(starts)), (0, fifth), 8, "用长根音和五音支撑第三拍军鼓"
    is_rock = conductor.profile and conductor.profile.genre in {
        "rock",
        "hard_rock",
        "alternative_rock",
    }
    if is_rock:
        starts = QUARTERS if density < 0.45 else tuple(range(0, BAR_TICKS, EIGHTH))
        degrees = (0, 0, 0, fifth) if approach == "root" else (0, 0, fifth, 12)
        return _bass_pulses(context, set(starts)), degrees, 8, "直八分重复根音，以五音或八度换气"
    starts = BASS_SYNCOPATION["bossa"] if approach == "syncopated" else QUARTERS[::2]
    degrees = (0, third, fifth, last) if approach == "walking" else (0, fifth)
    return _bass_pulses(context, set(starts)), degrees, 8, "用根音与底鼓对齐，为旋律留出空间"


class DrumAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        density = context.settings.density
        groove = _rhythm(context)
        style = groove if groove in {"swing", "bossa"} else context.settings.conductor.style
        approach = context.settings.conductor.drums
        rng = context.random_for("drums")
        offbeat = _offbeat(groove)
        meter = context.settings.meter
        genre = context.settings.genre
        figure = RHYTHM_FIGURES.get(groove) if meter == "4/4" else None
        if genre in {"cinematic", "ambient", "classical"} and meter == "4/4":
            cymbal = 42
            if genre == "cinematic":
                pattern = [(0, 36, 55), (2 * PPQ, 41, 47)]
                intent = "低力度底鼓和落地嗵鼓标记乐句"
            elif genre == "ambient":
                pattern = [(0, 42, 33), (2 * PPQ, 42, 30)]
                intent = "极轻的踩镲脉冲，给持续声部留空间"
            else:
                pattern = [(0, 37, 35)] if context.bar % 2 == 0 else []
                intent = "用极轻的边击标记乐句起点"
        elif genre == "hip_hop" and groove == "half_time" and meter == "4/4":
            cymbal = 42
            pattern = [(0, 36, 88), (PPQ + EIGHTH, 36, 64), (2 * PPQ, 38, 84)]
            pattern.extend(
                (tick, cymbal, 56 if tick % PPQ == 0 else 43)
                for tick in range(0, BAR_TICKS, EIGHTH)
            )
            if density > 0.58:
                pattern.extend(
                    (tick, cymbal, 44) for tick in (3 * PPQ + SIXTEENTH, 3 * PPQ + 3 * SIXTEENTH)
                )
            intent = "底鼓切分、第三拍军鼓、八分踩镲"
        elif meter != "4/4":
            pattern, cymbal, intent = _meter_drums(context.settings)
        elif figure:
            pattern = _profile_drums(context, groove, figure, rng)
            cymbal, intent = figure.cymbal, figure.intent
        else:
            pattern = list(DRUM_GROOVES[style])
            cymbal, beats = DRUM_TIMEKEEPING[style]
            intent = DRUM_INTENT[style]
            cymbal_beats = beats[::2] if approach == "sparse" else beats
            pattern.extend(
                (tick, cymbal, 65 + (index % 2) * 5) for index, tick in enumerate(cymbal_beats)
            )
            if style == "swing":
                pattern.extend((tick, 37 if density < 0.4 else 38, 55) for tick in BACKBEATS)
            if approach != "sparse":
                pattern.extend(
                    (tick + offbeat, cymbal, 48) for tick in beats if rng.random() < density
                )
                if style == "swing" and density >= 0.55 and context.bar % 4 == 3:
                    pattern.extend(((3 * PPQ + offbeat, 45, 72), (3 * PPQ + PPQ // 3, 38, 62)))
                elif style == "swing" and density >= 0.7:
                    pattern.append((PPQ + offbeat, 36, 61))
            if approach == "driving":
                pattern.extend(((PPQ + EIGHTH, 36, 75), (3 * PPQ + EIGHTH, 46, 68)))
        player_intent = None
        if phrase := context.player_phrase:
            if phrase.activity == "busy":
                timekeeping = (
                    set(figure.timekeeping[::2])
                    if figure and groove in {"one_drop", "four_on_floor"}
                    else set()
                )
                pattern = [
                    (tick, pitch, velocity - (10 if pitch in {cymbal, 44, 46} else 4))
                    for tick, pitch, velocity in pattern
                    if pitch == 36
                    or tick % context.settings.pulse_ticks == 0
                    or (pitch == cymbal and tick in timekeeping)
                ]
                player_intent = "收掉装饰音，保留主律动" if figure else "收掉装饰音，保留底鼓和正拍"
            elif (answer := _answer_tick(phrase, offbeat, context.settings)) is not None:
                # A rim click leaves room around the player's attack and does not crowd the kick.
                velocity = 62 if phrase.velocity == "strong" else 51
                pattern.append((answer, 37, velocity))
                player_intent = "用一记边击回应旋律重音"
            else:
                pattern = [
                    (tick, pitch, velocity - (8 if pitch == cymbal else 0))
                    for tick, pitch, velocity in pattern
                ]
                player_intent = "放轻镲片，让出旋律尾句"
        hits = {(tick, pitch): velocity for tick, pitch, velocity in pattern}
        modern = context.settings.lineup == "modern"
        acoustic = modern and genre not in {"cinematic", "ambient", "classical", "electronic"}
        if acoustic and (phrase is None or phrase.activity != "busy"):
            if meter == "4/4" and genre in {
                "rock",
                "hard_rock",
                "alternative_rock",
                "pop",
                "country",
            }:
                if context.settings.density >= 0.48 and approach != "sparse":
                    hits.setdefault((PPQ + 3 * SIXTEENTH, 38), 32)
                if (
                    context.section
                    and context.section_bar == 0
                    and context.section.texture == "full"
                ):
                    hits.pop((0, 42), None)
                    hits[0, 49] = 80
                if (
                    _rock_band(context)
                    and context.section is None
                    and context.bar % CHORUS_BARS == 0
                    and context.settings.density >= 0.5
                    and approach != "sparse"
                ):
                    hits.pop((0, 42), None)
                    hits[0, 49] = 84
                if _rock_band(context) and _rock_opened(context) and cymbal == 42:
                    hits = {
                        (tick, 51 if pitch == 42 else pitch): velocity + (4 if pitch == 42 else 0)
                        for (tick, pitch), velocity in hits.items()
                        if pitch != 46
                    }
                    intent = "副歌移到叮叮镲，二、四拍重军鼓"
                at_phrase_end = (context.section_bar if context.section else context.bar) % 4 == 3
                if at_phrase_end and context.settings.density >= 0.5 and approach != "sparse":
                    tail = (3 * PPQ + EIGHTH, 3 * PPQ + 3 * SIXTEENTH)
                    for tick, pitch, velocity in zip(tail, (47, 45), (65, 76), strict=True):
                        hits.pop((tick, cymbal), None)
                        hits.setdefault((tick, pitch), velocity)
            for (tick, pitch), velocity in tuple(hits.items()):
                if pitch in {42, 44, 51} and tick % context.settings.pulse_ticks:
                    varied = 2 if (context.bar + tick // EIGHTH) % 2 else -8
                    hits[tick, pitch] = velocity + varied
        ordered_hits = (
            sorted(hits.items())[: TRACK_DEFINITIONS["drums"].max_notes] if modern else hits.items()
        )
        return AgentOutput(
            intent,
            tuple(
                _note("drums", context, tick, 90, pitch, velocity + rng.randint(-6, 6))
                for (tick, pitch), velocity in ordered_hits
            ),
            player_intent=player_intent,
        )


class BassAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        density = context.settings.density
        chord = context.chord
        groove = _rhythm(context)
        style = groove if groove in {"swing", "bossa"} else context.settings.conductor.style
        meter = context.settings.meter
        uses_profile = meter != "4/4" or groove in RHYTHM_FIGURES
        approach = context.settings.conductor.bass
        rng = context.random_for("bass")
        fifth = chord.intervals[2]
        offbeat = _offbeat(groove)
        genre = context.settings.genre
        if genre in {"cinematic", "ambient", "classical"}:
            starts, degrees, gate = (0,), (0,), 9
            intent = "用长低音托住上方声部"
        elif genre == "country" and meter == "4/4":
            starts, degrees, gate = QUARTERS, (0, fifth), 7
            intent = "根音、五音交替，保持乡村低音脉冲"
        elif genre == "hip_hop" and meter == "4/4":
            starts, degrees, gate = (0, PPQ + EIGHTH, 3 * PPQ), (0, 0, fifth), 9
            intent = "长低音跟随切分底鼓"
        elif meter != "4/4":
            starts, degrees, gate, intent = _meter_bass(context.settings, fifth)
        elif uses_profile:
            starts, degrees, gate, intent = _profile_bass(context, groove, fifth)
        else:
            if approach == "root":
                starts, degrees = QUARTERS[::2], (0, fifth)
            else:
                third = chord.intervals[1]
                cadence = chord.intervals[-1] if len(chord.intervals) >= 4 else fifth
                degrees = (0, fifth, third, cadence) if density >= 0.35 else (0, fifth)
                if approach == "syncopated":
                    pulse = set(BASS_SYNCOPATION[style])
                else:
                    pulse = set(QUARTERS if density >= 0.35 else QUARTERS[::2])
                    if density >= 0.75:
                        pulse.add(3 * PPQ + offbeat)
                starts = _bass_pulses(context, pulse)
            gate = {"swing": 8, "bossa": 7, "funk": 6, "ballad": 9}[style]
            intent = BASS_INTENT[approach]
        low, high = (28 if uses_profile else 32), 54
        player_intent = None
        if phrase := context.player_phrase:
            if meter != "4/4":
                essentials = (0, context.settings.bar_ticks - context.settings.pulse_ticks)
            else:
                essentials = (EIGHTH, 2 * PPQ) if groove == "one_drop" else QUARTERS[::2]
            if phrase.pitch_register == "low":
                low, high = 28, 41
                starts, degrees = essentials, (0, fifth)
                player_intent = "缩到低音区，用根音和五音避让"
            elif phrase.activity == "busy":
                starts, degrees = essentials, (0, fifth)
                player_intent = (
                    "收成两次根音与五音，减少走动"
                    if groove == "one_drop"
                    else "回到两拍根音与五音，减少走动"
                )
            else:
                answer = _answer_tick(phrase, offbeat, context.settings)
                if (
                    answer is not None
                    and len(starts) < TRACKS["bass"].max_notes
                    and all(abs(answer - tick) >= PPQ // 4 for tick in starts)
                ):
                    starts = tuple(sorted((*starts, answer)))
                    player_intent = "在旋律空隙用一个和弦音回应"
                else:
                    player_intent = "缩短低音收尾，为旋律留出空隙"
            gate = max(4, gate - 2)
        notes = []
        anchor = _in_register(chord.root, low, high - 12 if 12 in degrees else high, 38)
        next_chord = context.next_chord
        if (
            context.voice_leading
            and next_chord is None
            and (context.settings.loop or context.bar % CHORUS_BARS < CHORUS_BARS - 1)
        ):
            next_chord = context.settings.chord_at(context.bar + 1)
        pickup_pitch = None
        if (
            context.voice_leading
            and approach != "root"
            and (meter != "4/4" or groove not in {"one_drop", "four_on_floor"})
            and next_chord is not None
            and chord.bass != next_chord.bass
            and (phrase is None or (phrase.activity != "busy" and phrase.pitch_register != "low"))
        ):
            final_pulse = context.settings.bar_ticks - context.settings.pulse_ticks
            pickup = final_pulse + (2 * EIGHTH if meter == "6/8" else offbeat)
            if len(starts) == 2 and phrase is None and starts[-1] < pickup:
                starts = (*starts, pickup)
            if starts[-1] >= final_pulse:
                pickup_pitch = _bass_approach(
                    chord, next_chord, low, high, 38 if uses_profile else 42
                )
                intent += "，尾音靠向下一和弦的低音"
        for index, (tick, next_tick) in enumerate(
            zip(starts, (*starts[1:], context.settings.bar_ticks), strict=True)
        ):
            degree = degrees[index % len(degrees)]
            pitch_class = chord.bass if index == 0 else chord.root + degree
            if uses_profile:
                target = anchor + degree
            else:
                target = 42 if index == 0 else 44 + rng.randint(-4, 5)
            if context.voice_leading and index == 0 and uses_profile and 12 not in degrees:
                target = 38
            if index == len(starts) - 1 and pickup_pitch is not None:
                pitch = pickup_pitch
            else:
                pitch = _in_register(pitch_class, low, high, target)
            notes.append(
                _note(
                    "bass",
                    context,
                    tick,
                    (next_tick - tick) * gate // 10,
                    pitch,
                    (82 if tick in context.kicks else 71) + rng.randint(-5, 5),
                )
            )
        return AgentOutput(intent, tuple(notes), player_intent=player_intent)


class PianoAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        density = context.settings.density
        chord = context.chord
        conductor = context.settings.conductor
        groove = _rhythm(context)
        style = groove if groove in {"swing", "bossa"} else conductor.style
        approach = conductor.piano
        rng = context.random_for("piano")
        intent = PIANO_INTENT[approach]
        open_fifth = (
            conductor.profile is not None
            and conductor.profile.genre == "hard_rock"
            and groove in {"straight_eighth", "half_time"}
        )
        if open_fifth:
            guide_tones = (
                [0, chord.intervals[2], 12] if density >= 0.45 else [0, chord.intervals[2]]
            )
        else:
            guide_tones = list(_piano_core(chord, open_fifth))
            if density >= 0.45 or groove in {"straight_eighth", "one_drop", "half_time"}:
                guide_tones.append(chord.intervals[-1] if len(chord.intervals) >= 5 else 12)
            if density >= 0.8:
                guide_tones.append(chord.intervals[2])
        if context.settings.meter != "4/4":
            starts, intent = _meter_piano(context.settings)
        elif approach == "sustained":
            starts = ((0, 3 * PPQ + EIGHTH),)
        elif figure := PIANO_FIGURES.get(groove):
            starts = figure
            intent = PIANO_FIGURE_INTENT[groove]
            if groove == "straight_eighth" and approach == "syncopated":
                starts = ((EIGHTH, EIGHTH), (PPQ + EIGHTH, EIGHTH), (3 * PPQ, EIGHTH))
        elif approach == "syncopated":
            offbeat = _offbeat(groove)
            starts = ((offbeat, PPQ // 3), (PPQ + offbeat, SIXTEENTH), (3 * PPQ, PPQ // 3))
        elif style == "swing" and density < 0.35:
            starts = ((PPQ + SWUNG_EIGHTH, PPQ),)
        else:
            starts = PIANO_COMP[style]
            if (
                style == "swing"
                and density >= 0.75
                and context.drum_notes + context.bass_notes < 19
            ):
                starts = (*starts, (3 * PPQ + SWUNG_EIGHTH, SIXTEENTH))
        if open_fifth:
            intent = "钢琴用根音和五音弹开放五度"
        low = max(59 if context.voice_leading else 54, context.bass_high + 5)
        high, center, velocity = 79, 62, 66
        if context.section is not None and context.settings.lineup == "trio":
            high = 73
        player_intent = None
        if phrase := context.player_phrase:
            guide_tones = guide_tones[:2]
            if phrase.pitch_register == "low":
                low, high, center = 65, 79, 68
                register_intent = "上移和弦"
            else:
                high, center = max(67, low + 11), low + 2
                register_intent = "压低和弦" if phrase.pitch_register == "high" else "收窄和弦"
            space = (
                tuple(start for start in starts if abs(start[0] - phrase.accent_tick) >= PPQ // 3)
                if phrase.accent_tick is not None
                else starts
            )
            if phrase.activity == "busy":
                tick, duration = (space or starts)[0]
                starts = ((tick, min(duration, PPQ // 2)),)
                player_intent = f"{register_intent}，只留一次短导音"
            else:
                starts = tuple((tick, min(duration, PPQ)) for tick, duration in (space or starts))
                player_intent = f"{register_intent}，用轻奏导音答句"
            velocity = 59 if phrase.velocity == "strong" else 54
        if _rock_band(context) and phrase is None and context.section is None:
            hold = PPQ * 2 - EIGHTH // 2
            starts = ((0, hold), (2 * PPQ, hold)) if density >= 0.6 else ((0, BAR_TICKS - EIGHTH),)
            pitches = sorted(
                {_in_register(chord.root + tone, 60, 76, 67) for tone in chord.intervals[:3]}
            )
            velocity = 58
            intent = "长音三和弦托在吉他上方，不与吉他抢节奏"
        elif context.voice_leading:
            pitches = _connected_piano(context, guide_tones, low, high, center, open_fifth)
            intent += "，换和弦时保留邻近声部"
        else:
            pitches = sorted(
                {
                    _in_register(
                        chord.root + step, low, high, center + (step if open_fifth else step // 2)
                    )
                    for step in guide_tones
                }
            )
        accompaniment = tuple(
            _note("piano", context, tick, duration, pitch, velocity + rng.randint(-6, 6))
            for tick, duration in starts
            for pitch in pitches
        )
        if context.settings.genre == "classical" and phrase is None and context.section is None:
            notes = (chord.intervals[0], chord.intervals[2], chord.intervals[1], chord.intervals[2])
            accompaniment = tuple(
                _note(
                    "piano",
                    context,
                    tick,
                    EIGHTH * 3 // 4,
                    _in_register(chord.root + notes[index % 4], 58, 77, 65 + index % 4),
                    60,
                )
                for index, tick in enumerate(range(0, context.settings.bar_ticks, EIGHTH))
            )
            intent = "八分分解和弦与固定手型"
        if context.settings.cinematic and (phrase is None or phrase.activity != "busy"):
            pulses = (
                QUARTERS
                if context.settings.meter == "4/4"
                else range(0, context.settings.bar_ticks, context.settings.pulse_ticks)
            )
            contour = (0, chord.intervals[2], chord.intervals[1], chord.intervals[2])
            pulse_notes = tuple(
                _note(
                    "piano",
                    context,
                    tick + EIGHTH,
                    EIGHTH * 3 // 4,
                    _in_register(chord.root + contour[index], 77, 88, 83),
                    57,
                )
                for index, tick in enumerate(pulses)
                if tick + EIGHTH < context.settings.bar_ticks
                and (
                    not phrase
                    or phrase.accent_tick is None
                    or abs(tick + EIGHTH - phrase.accent_tick) >= PPQ // 3
                )
            )
            accompaniment += tuple(
                note
                for note in pulse_notes
                if all(
                    part.pitch != note.pitch
                    or part.start_tick + part.duration_ticks <= note.start_tick
                    or note.start_tick + note.duration_ticks <= part.start_tick
                    for part in accompaniment
                )
            )
            intent = "持续低声部与高音区反复动机"
        melody = (
            _song_motif("piano", context)
            if context.section is not None and context.settings.lineup == "trio"
            else ()
        )
        if melody:
            # A melodic pitch owns its overlap; leave the other chord voices in place.
            accompaniment = tuple(
                note
                for note in accompaniment
                if not any(
                    note.pitch == lead.pitch
                    and note.start_tick < lead.start_tick + lead.duration_ticks
                    and lead.start_tick < note.start_tick + note.duration_ticks
                    for lead in melody
                )
            )[: TRACKS["piano"].max_notes - len(melody)]
            intent += "，上声部用重复动机作问答"
        return AgentOutput(
            intent,
            (*accompaniment, *melody),
            player_intent=player_intent,
        )


_GUITAR_STRIKES = {
    "straight_eighth": (0, PPQ + EIGHTH, 2 * PPQ, 3 * PPQ + EIGHTH),
    "half_time": (0, 2 * PPQ + EIGHTH),
    "swing": (PPQ + SWUNG_EIGHTH, 3 * PPQ),
    "shuffle": (PPQ, 2 * PPQ + SWUNG_EIGHTH, 3 * PPQ),
    "bossa": (EIGHTH, 2 * PPQ, 3 * PPQ + EIGHTH),
    "funk": (3 * SIXTEENTH, PPQ + EIGHTH, 2 * PPQ + 3 * SIXTEENTH, 3 * PPQ + EIGHTH),
    "sixteenth": (3 * SIXTEENTH, PPQ + EIGHTH, 2 * PPQ + 3 * SIXTEENTH, 3 * PPQ + EIGHTH),
    "ballad": (EIGHTH, 2 * PPQ),
    "one_drop": OFFBEATS,
    "four_on_floor": OFFBEATS,
}


class RhythmGuitarAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        if context.settings.genre in {"cinematic", "ambient", "classical"}:
            return AgentOutput("让出中频给持续键盘与低音", ())
        if _rock_band(context):
            return self._rock(context)
        groove = _rhythm(context)
        chord = context.chord
        profile = context.settings.conductor.profile
        rock = profile is not None and profile.genre in {"rock", "hard_rock", "alternative_rock"}
        meter = context.settings.meter
        if meter == "3/4":
            strikes = (PPQ, 2 * PPQ, 2 * PPQ + EIGHTH)
        elif meter == "6/8":
            strikes = (EIGHTH, 2 * EIGHTH, 4 * EIGHTH, 5 * EIGHTH)
        else:
            strikes = _GUITAR_STRIKES[groove]
        doubled = sum(tick in context.piano_onsets for tick in strikes)
        if doubled == len(strikes) and len(strikes) > 2:
            # When both instruments own the same offbeat slot, trade accents by bar.
            strikes = strikes[context.bar % 2 :: 2]
        elif 0 < doubled < len(strikes) and not rock:
            strikes = tuple(tick for tick in strikes if tick not in context.piano_onsets)
        if context.settings.density < 0.45 or (
            context.player_phrase is not None and context.player_phrase.activity == "busy"
        ):
            strikes = strikes[::2]
        elif context.section and context.section.texture == "answer" and context.section_bar % 2:
            strikes = strikes[: max(1, (len(strikes) + 1) // 2)]

        if rock:
            root = _in_register(chord.root, 48, 59, 53)
            voicing = root, root + chord.intervals[2], root + 12
            if (
                profile.genre != "hard_rock"
                and context.section
                and context.section.texture == "full"
                and root + 12 + chord.intervals[1] <= TRACK_DEFINITIONS["rhythm_guitar"].max_pitch
            ):
                voicing = (*voicing, root + 12 + chord.intervals[1])
        else:
            upper = chord.intervals[min(3, len(chord.intervals) - 1)]
            root = _in_register(chord.root, 48, 59, 54)
            voicing = (root, root + chord.intervals[1], root + upper)
        offbeat_chops = groove in {"one_drop", "bossa", "funk", "sixteenth"}
        speed = max(9, round(context.settings.bpm * PPQ * 0.008 / 60))
        rng = context.random_for("rhythm_guitar")
        notes = []
        for index, tick in enumerate(strikes):
            up = bool(tick % context.settings.pulse_ticks)
            muted = rock and up and (index + context.bar) % 3 != 1
            ordered = sorted(voicing, reverse=up)
            gate = (
                SIXTEENTH // 2
                if offbeat_chops or muted
                else (EIGHTH + SIXTEENTH if rock else EIGHTH)
            )
            next_tick = (
                strikes[index + 1] if index + 1 < len(strikes) else context.settings.bar_ticks
            )
            for string, pitch in enumerate(ordered):
                onset = tick + string * speed
                if onset >= next_tick:
                    break
                force = (70 if rock else 59) + (5 if not up else -3) - string * 2
                notes.append(
                    _note(
                        "rhythm_guitar",
                        context,
                        onset,
                        min(gate, next_tick - onset),
                        pitch,
                        force + rng.randint(-3, 3),
                        articulation="palm_mute" if muted else "normal",
                    )
                )
        return AgentOutput(
            "下拨开放五度，反拍上扫或闷弦" if rock else "错开键盘，带轻重音上下扫弦",
            tuple(notes),
        )

    def _rock(self, context: BarContext) -> AgentOutput:
        chord = context.chord
        root = _in_register(chord.root, 48, 59, 52)
        voicing = (root, root + chord.intervals[2], root + 12)
        speed = max(6, round(context.settings.bpm * PPQ * 0.004 / 60))
        busy = context.player_phrase is not None and context.player_phrase.activity == "busy"
        if _rock_opened(context):
            # Ring on one, push the and-of-two, ring on three, push the and-of-four.
            hits = ((0, 92, False), (3 * EIGHTH, 82, False), (4 * EIGHTH, 90, False))
            hits += ((7 * EIGHTH, 80, False),)
            intent = "副歌放开强力和弦：一拍、二拍后半、三拍、四拍后半"
        else:
            # Palm-muted eighths with the downbeats of one and three left open.
            hits = tuple(
                (index * EIGHTH, 90 if index % 4 == 0 else 72, index % 4 != 0) for index in range(8)
            )
            if context.settings.density < 0.45:
                hits = hits[::2]
            intent = "八分闷音强力和弦推进，一、三拍放开重音"
        if busy:
            hits = hits[::2]
            intent = "让出旋律，强力和弦只留重拍"
        rng = context.random_for("rhythm_guitar")
        notes = []
        for index, (tick, force, muted) in enumerate(hits):
            next_tick = hits[index + 1][0] if index + 1 < len(hits) else BAR_TICKS
            length = SIXTEENTH if muted else next_tick - tick - 30
            # Chugs are all downstrokes; an open push off the beat is an upstroke.
            upstroke = not muted and tick % PPQ != 0
            for string, pitch in enumerate(sorted(voicing, reverse=upstroke)):
                onset = tick + string * speed
                notes.append(
                    _note(
                        "rhythm_guitar",
                        context,
                        onset,
                        max(30, min(length, next_tick - onset - 10)),
                        pitch,
                        force - string * 3 + rng.randint(-3, 3),
                        articulation="palm_mute" if muted else "normal",
                    )
                )
        return AgentOutput(intent, tuple(notes))


_PENTATONIC = (0, 3, 5, 7, 10)
# Rock licks as steps along the minor pentatonic, from where the lick starts.
_ROCK_LICKS = (
    (4, 3, 2, 1, 0, 0),
    (0, 1, 2, 1, 0, -1),
    (2, 3, 4, 5, 4, 2),
    (-1, 0, 1, 2, 3, 2),
)


def _rock_lick(context: BarContext) -> tuple[str, tuple[Note, ...]] | None:
    """A pentatonic fill over the last two beats of each four-bar phrase."""
    if context.bar % 4 != 3 or context.settings.density < 0.35:
        return None
    # The key comes from the chords being played, which may differ from the style default.
    tonic = parse_chord(context.settings.progression[0]).root
    rng = context.random_for("lead_guitar")
    shape = _ROCK_LICKS[rng.randrange(len(_ROCK_LICKS))]
    scale = [pitch for pitch in range(60, 89) if (pitch - tonic) % 12 in _PENTATONIC]
    home = min(range(len(scale)), key=lambda index: abs(scale[index] - 72))
    climax = context.bar % CHORUS_BARS == CHORUS_BARS - 1
    notes = []
    for index, step in enumerate(shape):
        position = max(0, min(len(scale) - 1, home + step + (2 if climax else 0)))
        last = index == len(shape) - 1
        # Five sixteenths from beat three, then a held note on the and-of-four.
        tick = 2 * PPQ + (index + (1 if last else 0)) * SIXTEENTH
        duration = (BAR_TICKS - tick - 20) if last else SIXTEENTH - 10
        notes.append(
            _note(
                "lead_guitar",
                context,
                tick,
                duration,
                scale[position],
                (84 if last else 74) + (6 if climax else 0),
                articulation="harmonic"
                if last and climax
                else "legato"
                if 0 < index and not last
                else "normal",
            )
        )
    return "句尾用小调五声音阶加花，长音收在下一句前", tuple(notes)


class LeadGuitarAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        if (
            context.settings.genre in {"cinematic", "ambient", "classical"}
            and context.section is None
        ):
            return AgentOutput("键盘领奏，吉他暂时留白", ())
        phrase = context.player_phrase
        if phrase is not None and phrase.activity == "busy":
            return AgentOutput("留出整句，让键盘主奏", ())
        if context.section is not None:
            notes = _song_motif("lead_guitar", context)
            if context.section.lead and context.section.lead[context.section_bar] is not None:
                return AgentOutput("按指挥写下的乐句领奏", notes)
            articulate = []
            for index, note in enumerate(notes):
                previous = notes[index - 1] if index else None
                gap = note.start_tick - previous.start_tick if previous else 0
                close = previous and abs(note.pitch - previous.pitch) <= 5 and gap <= PPQ * 3 // 2
                end = context.section_bar == context.section.bars - 1 and index == len(notes) - 1
                technique: Articulation = (
                    "harmonic"
                    if end and context.section.role in {"intro", "outro", "bridge"}
                    else "legato"
                    if close and previous.pitch != note.pitch
                    else "normal"
                )
                if technique == "legato" and articulate and articulate[-1].pitch != note.pitch:
                    held = articulate[-1]
                    articulate[-1] = held.model_copy(update={"duration_ticks": gap + 10})
                articulate.append(note.model_copy(update={"articulation": technique}))
            return AgentOutput(
                "动机问答，近距离音用连音，留白段以泛音收句",
                tuple(articulate),
            )
        settings = context.settings
        if _rock_band(context) and phrase is None:
            lick = _rock_lick(context)
            if lick is None:
                return AgentOutput("等句尾再加花，前面留给节奏吉他", ())
            return AgentOutput(lick[0], lick[1])
        meter = settings.meter
        offbeat = _offbeat(_rhythm(context))
        if phrase is not None:
            start = _answer_tick(phrase, offbeat, settings)
        elif meter != "4/4":
            # Two late subdivisions still leave room for a clear next downbeat.
            start = settings.bar_ticks - 2 * EIGHTH if context.bar % 2 else None
        else:
            start = (2 * PPQ + offbeat) if context.bar % 2 == 1 else None
        if start is None:
            return AgentOutput("等待后半句的回应位置", ())
        if any(abs(start - tick) < SIXTEENTH for tick in context.piano_onsets):
            start += SIXTEENTH
            if start >= settings.bar_ticks:
                return AgentOutput("句尾空间不足，等下一句进入", ())

        chord = context.chord
        anchor = 68 if phrase and phrase.pitch_register == "high" else 76
        entry = _in_register(chord.root + chord.intervals[2], 58, 86, anchor)
        next_chord = context.next_chord
        if next_chord is None and (
            context.settings.loop or context.bar % CHORUS_BARS < CHORUS_BARS - 1
        ):
            next_chord = context.settings.chord_at(context.bar + 1)
        target = (next_chord or chord).root + (next_chord or chord).intervals[1]
        destination = _in_register(target, 58, 88, entry)
        other_tones = {(chord.root + degree) % 12 for degree in chord.intervals}
        answer = min(
            (pitch for pitch in range(58, 89) if pitch != entry and pitch % 12 in other_tones),
            key=lambda pitch: (abs(pitch - destination) * 2 + abs(pitch - entry), pitch),
        )
        notes = [_note("lead_guitar", context, start, SIXTEENTH + PPQ // 12, entry, 73)]
        if (tail := start + EIGHTH) < settings.bar_ticks - SIXTEENTH // 2:
            technique = "harmonic" if context.bar % 8 == 7 else "legato"
            if technique == "legato":
                notes[0] = notes[0].model_copy(update={"duration_ticks": EIGHTH + 10})
            notes.append(
                _note("lead_guitar", context, tail, EIGHTH, answer, 63, articulation=technique)
            )
        return AgentOutput("短句回应，尾音向下一和弦靠拢", tuple(notes))


class SynthAgent:
    async def generate(self, context: BarContext) -> AgentOutput:
        return self.arrange(context)

    def arrange(self, context: BarContext) -> AgentOutput:
        phrase = context.player_phrase
        if phrase is not None and phrase.activity == "busy":
            return AgentOutput("为现场旋律留空，暂缓铺底", ())
        chord = context.chord
        prior = context.prior_chord or (
            context.settings.chord_at(context.bar - 1)
            if context.bar or context.settings.loop
            else chord
        )
        common = {(prior.root + degree) % 12 for degree in prior.intervals}
        tones = {(chord.root + degree) % 12 for degree in chord.intervals}
        pitches = range(64, 76)
        selected = sorted(
            (pitch for pitch in pitches if pitch % 12 in tones),
            key=lambda pitch: (
                pitch in context.piano_pitches,
                pitch % 12 not in common,
                abs(pitch - 69),
                pitch,
            ),
        )
        count = 2 if context.settings.density >= 0.65 and phrase is None else 1
        settings = context.settings
        tick = (
            settings.pulse_ticks
            if settings.meter != "4/4"
            else 2 * PPQ
            if _rhythm(context) == "one_drop"
            else PPQ
        )
        return AgentOutput(
            "挑选与前和弦相接的内声部，低力度维持长音",
            tuple(
                _note("synth", context, tick, settings.bar_ticks - tick, pitch, 47)
                for pitch in selected[:count]
            ),
        )


RULE_AGENTS = {
    "drums": DrumAgent(),
    "bass": BassAgent(),
    "piano": PianoAgent(),
    "rhythm_guitar": RhythmGuitarAgent(),
    "lead_guitar": LeadGuitarAgent(),
    "synth": SynthAgent(),
}


def _fallback_output(track: TrackId, context: BarContext) -> AgentOutput:
    simplified = context.settings.model_copy(update={"density": min(context.settings.density, 0.3)})
    return RULE_AGENTS[track].arrange(replace(context, settings=simplified))


class Planner:
    """Plan each musician independently, passing completed proposals through the bar context."""

    def __init__(
        self,
        agents: Mapping[TrackId, InstrumentAgent] | None = None,
        deadline: float = 0.12,
        *,
        participants: Mapping[TrackId, Participant] | None = None,
        negotiation_deadline: float = 0.06,
    ) -> None:
        if deadline <= 0:
            raise ValueError("Agent deadline must be positive")
        self.deadline = deadline
        self._agents: dict[TrackId, InstrumentAgent] = dict(RULE_AGENTS)
        if agents:
            if unknown := agents.keys() - TRACK_DEFINITIONS.keys():
                raise ValueError(f"Unknown tracks: {sorted(unknown)}")
            self._agents.update(agents)
        self._pending: dict[TrackId, set[asyncio.Task[AgentOutput]]] = {
            track: set() for track in self._agents
        }
        self._negotiator = Negotiator(participants, negotiation_deadline)
        self._defaults = dict(self._agents)

    def attach(self, track: TrackId, agent: InstrumentAgent) -> None:
        """Hand one track to another agent, such as a remote process."""
        if track not in self._agents:
            raise ValueError(f"Unknown track: {track}")
        self._agents[track] = agent

    def detach(self, track: TrackId, agent: InstrumentAgent) -> None:
        """Restore the original musician unless a newer agent already replaced it."""
        if self._agents.get(track) is agent:
            self._agents[track] = self._defaults[track]

    def agent(self, track: TrackId) -> InstrumentAgent:
        return self._agents[track]

    def fallback(
        self,
        bar: int,
        settings: MusicSettings,
        version: int,
        *,
        voice_leading: bool = False,
        prior_chord: Chord | None = None,
        next_chord: Chord | None = None,
        section: SongSection | None = None,
        section_bar: int = 0,
    ) -> BarPlan:
        context = BarContext(
            bar,
            settings,
            settings.chord_at(bar),
            voice_leading=voice_leading or settings.conductor.profile is not None,
            prior_chord=prior_chord,
            next_chord=next_chord,
            section=section,
            section_bar=section_bar,
        )
        notes = []
        intents = []
        tracks = active_tracks(settings.lineup)
        for track in tracks:
            output = _fallback_output(track, context)
            notes.extend(output.notes)
            intents.append(
                AgentDecision(
                    track_id=track,
                    intent=output.intent,
                    status="fallback",
                    detail="规划未在锁定前提交",
                )
            )
            if settings.collaboration != "solo":
                context = context.observe(track, output.notes)
        return BarPlan(
            bar=bar,
            version=version,
            notes=tuple(notes),
            fallback_tracks=tracks,
            intents=tuple(intents),
        )

    async def plan(
        self,
        bar: int,
        settings: MusicSettings,
        version: int,
        *,
        on_negotiation: Callable[[NegotiationEvent], None] | None = None,
        player_phrase: PlayerPhrase | None = None,
        voice_leading: bool = False,
        prior_chord: Chord | None = None,
        next_chord: Chord | None = None,
        section: SongSection | None = None,
        section_bar: int = 0,
    ) -> BarPlan:
        context = BarContext(
            bar,
            settings,
            settings.chord_at(bar),
            player_phrase=player_phrase,
            voice_leading=voice_leading or settings.conductor.profile is not None,
            prior_chord=prior_chord,
            next_chord=next_chord,
            section=section,
            section_bar=section_bar,
        )
        parts = {}
        player_intents = {}
        intents = []
        for track in active_tracks(settings.lineup):
            output, decision = await self._proposal(track, context)
            parts[track] = output.notes
            if player_phrase and track in TRACKS:
                player_intents[track] = output.player_intent or "沿用原伴奏"
            intents.append(decision)
            if settings.collaboration != "solo":
                context = context.observe(track, output.notes)
        ready = {intent.track_id for intent in intents if intent.status == "ready"}
        messages = ()
        if settings.collaboration == "negotiate":
            messages = await self._negotiator.run(
                bar,
                version,
                settings,
                parts,
                ready,
                on_negotiation,
            )
        return BarPlan(
            bar=bar,
            version=version,
            revision=1,
            notes=tuple(note for part in parts.values() for note in part),
            fallback_tracks=tuple(
                intent.track_id for intent in intents if intent.status != "ready"
            ),
            intents=tuple(intents),
            negotiation=messages,
            player_response=PlayerResponse(phrase=player_phrase, **player_intents)
            if player_phrase
            else None,
        )

    async def _proposal(
        self,
        track: TrackId,
        context: BarContext,
    ) -> tuple[AgentOutput, AgentDecision]:
        try:
            output = await self._generate(track, context)
            if not isinstance(output, AgentOutput) or not isinstance(output.notes, tuple):
                raise TypeError("Expected AgentOutput with an immutable note tuple")
            for note in output.notes:
                if note.track_id != track or not note.event_id.startswith(
                    f"{track}-{context.bar}-"
                ):
                    raise ValueError("Event belongs to another track or bar")
                Note.model_validate(note)
                if note.start_tick + note.duration_ticks > context.settings.bar_ticks:
                    raise ValueError("Note crosses the current measure")
            ordered = validate_notes(context.bar, output.notes)
            output = replace(output, notes=ordered)
            decision = AgentDecision(track_id=track, intent=output.intent, status="ready")
        except _AgentBusy as error:
            status, detail = "busy", str(error)
        except TimeoutError as error:
            status, detail = "timeout", str(error)
        except (ValueError, AttributeError, TypeError) as error:
            status, detail = "invalid", str(error)[:240]
        # A third-party agent failure must leave the other musicians' proposals intact.
        except Exception as error:  # noqa: BLE001
            status, detail = "failed", type(error).__name__
        else:
            return output, decision
        output = _fallback_output(track, context)
        return output, AgentDecision(
            track_id=track,
            intent=output.intent,
            status=status,
            detail=detail,
        )

    async def _generate(self, track: TrackId, context: BarContext) -> AgentOutput:
        pending = self._pending[track]
        if len(pending) >= 4:
            raise _AgentBusy("乐手已有四个规划任务在运行")
        agent = self._agents[track]
        deadline = getattr(agent, "deadline", None) or self.deadline
        task = asyncio.create_task(agent.generate(context))
        pending.add(task)

        def retire(finished: asyncio.Task[AgentOutput]) -> None:
            pending.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(retire)
        try:
            done, _ = await asyncio.wait((task,), timeout=deadline)
        finally:
            if not task.done():
                task.cancel()
        if not done:
            raise TimeoutError(f"规划超过 {deadline * 1000:g} ms")
        if task.cancelled():
            raise RuntimeError("Agent cancelled its own proposal")
        return task.result()


class _AgentBusy(Exception):
    pass
