"""Shared musical settings, chord parsing, and immutable bar plans."""

import re
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

PPQ = 960
BEATS_PER_BAR = 4
BAR_TICKS = PPQ * BEATS_PER_BAR
CHORUS_BARS = 8
PLAYER_CHANNEL = 2
TrioTrackId = Literal["drums", "bass", "piano"]
TrackId = Literal[TrioTrackId, "rhythm_guitar", "lead_guitar", "synth"]
Lineup = Literal["trio", "modern"]
TimeSignature = Literal["4/4", "3/4", "6/8"]
TIME_SIGNATURES: dict[TimeSignature, tuple[int, int]] = {
    "4/4": (4, 4),
    "3/4": (3, 4),
    "6/8": (6, 8),
}
Style = Literal["swing", "bossa", "funk", "ballad"]
Genre = Literal[
    "swing",
    "bossa",
    "funk",
    "ballad",
    "rock",
    "hard_rock",
    "alternative_rock",
    "pop",
    "blues",
    "reggae",
    "disco",
    "soul",
    "cinematic",
    "ambient",
    "electronic",
    "latin",
    "country",
    "hip_hop",
    "classical",
]
Groove = Literal[
    "swing",
    "bossa",
    "straight_eighth",
    "sixteenth",
    "shuffle",
    "one_drop",
    "four_on_floor",
    "half_time",
]
Mode = Literal["major", "minor", "dorian", "mixolydian"]
Harmony = Literal[
    "keep",
    "pop_1564",
    "minor_1645",
    "mixolydian_174",
    "blues_8",
    "dorian_vamp",
    "minor_1767",
    "soul_251",
    "major_145",
]
HARMONY_MODES: dict[Harmony, Mode] = {
    "pop_1564": "major",
    "minor_1645": "minor",
    "mixolydian_174": "mixolydian",
    "blues_8": "major",
    "dorian_vamp": "dorian",
    "minor_1767": "minor",
    "soul_251": "major",
    "major_145": "major",
}
Collaboration = Literal["solo", "shared", "negotiate"]


@dataclass(frozen=True, slots=True)
class Track:
    title: str
    channel: int
    program: int
    min_pitch: int
    max_pitch: int
    max_notes: int

    @property
    def name(self) -> str:
        return self.title


TRACKS: MappingProxyType[TrackId, Track] = MappingProxyType(
    {
        "drums": Track("Drums", 9, 0, 35, 59, 32),
        "bass": Track("Bass", 1, 32, 28, 55, 12),
        "piano": Track("Piano", 0, 0, 48, 88, 32),
    }
)

TRACK_DEFINITIONS: MappingProxyType[TrackId, Track] = MappingProxyType(
    {
        **TRACKS,
        "rhythm_guitar": Track("Rhythm Guitar", 3, 27, 48, 76, 24),
        "lead_guitar": Track("Lead Guitar", 4, 30, 55, 88, 12),
        "synth": Track("Synth Pad", 5, 89, 55, 88, 16),
    }
)


def active_tracks(lineup: Lineup) -> tuple[TrackId, ...]:
    return tuple(TRACK_DEFINITIONS if lineup == "modern" else TRACKS)


DEFAULT_PROGRESSION = ("Dm7", "G7", "Cmaj7", "Am7", "Dm7", "G7", "Em7", "A7")

_NATURAL = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACCIDENTAL = {"#": 1, "b": -1, "": 0}
_QUALITIES = {
    "": (0, 4, 7),
    "m": (0, 3, 7),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "mMaj7": (0, 3, 7, 11),
    "m7b5": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9),
    "7sus4": (0, 5, 7, 10),
    "7b5": (0, 4, 6, 10),
    "7#5": (0, 4, 8, 10),
    "add9": (0, 4, 7, 14),
    "madd9": (0, 3, 7, 14),
    "9": (0, 4, 7, 10, 14),
    "maj9": (0, 4, 7, 11, 14),
    "m9": (0, 3, 7, 10, 14),
    "7b9": (0, 4, 7, 10, 13),
    "7#9": (0, 4, 7, 10, 15),
    "13": (0, 4, 7, 10, 14, 21),
}
_ALIASES = {
    "M": "",
    "maj": "",
    "major": "",
    "min": "m",
    "-": "m",
    "min6": "m6",
    "M7": "maj7",
    "M9": "maj9",
    "min7": "m7",
    "-7": "m7",
    "-9": "m9",
    "mmaj7": "mMaj7",
    "mM7": "mMaj7",
    "ø": "m7b5",
    "ø7": "m7b5",
    "°": "dim",
    "°7": "dim7",
    "+": "aug",
    "sus": "sus4",
    "7sus": "7sus4",
    "Δ": "",
    "Δ7": "maj7",
    "Δ9": "maj9",
}
_CHORD = re.compile(r"([A-Ga-g])([#b]?)([^/]*)(?:/([A-Ga-g])([#b]?))?")


@dataclass(frozen=True, slots=True)
class Chord:
    symbol: str
    root: int
    bass: int
    intervals: tuple[int, ...]


def parse_chord(symbol: str) -> Chord:
    """Parse a chord symbol into pitch classes and canonical spelling."""
    normalized = symbol.strip().translate(str.maketrans("♭♯−∆△", "b#-ΔΔ"))
    match = _CHORD.fullmatch(normalized)
    if match is None:
        raise ValueError(f"Invalid chord '{symbol}'")
    letter, accidental, raw_quality, bass_letter, bass_accidental = match.groups()
    quality = _ALIASES.get(raw_quality, raw_quality)
    if quality not in _QUALITIES:
        raise ValueError(f"Unsupported chord quality '{raw_quality}' in '{symbol}'")
    root = (_NATURAL[letter.upper()] + _ACCIDENTAL[accidental]) % 12
    name = f"{letter.upper()}{accidental}{quality}"
    if bass_letter is None:
        return Chord(name, root, root, _QUALITIES[quality])
    bass = (_NATURAL[bass_letter.upper()] + _ACCIDENTAL[bass_accidental]) % 12
    return Chord(f"{name}/{bass_letter.upper()}{bass_accidental}", root, bass, _QUALITIES[quality])


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MusicProfile(FrozenModel):
    """Musical direction independent of the four legacy instrument presets."""

    genre: Genre
    groove: Groove
    mode: Mode
    harmony: Harmony
    tonic: str = Field(min_length=1, max_length=2)
    reference: str | None = Field(default=None, min_length=1, max_length=100)
    basis: Literal["genre", "reference", "inferred", "theory"]

    @field_validator("tonic")
    @classmethod
    def normalize_tonic(cls, tonic: str) -> str:
        if not re.fullmatch(r"[A-Ga-g][#b♯♭]?", tonic):
            raise ValueError("Expected a note name for the tonic")
        return parse_chord(tonic).symbol

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.harmony != "keep" and HARMONY_MODES[self.harmony] != self.mode:
            raise ValueError("The chord formula and mode must agree")
        return self


class ConductorPlan(FrozenModel):
    """High-level choices that the three musicians can translate into notes."""

    style: Style = "swing"
    intensity: int = Field(default=55, ge=0, le=100, strict=True)
    drums: Literal["balanced", "sparse", "driving"] = "balanced"
    bass: Literal["walking", "root", "syncopated"] = "walking"
    piano: Literal["comp", "sustained", "syncopated"] = "comp"
    profile: MusicProfile | None = Field(default=None, exclude_if=lambda profile: profile is None)


class ImprovisationPhrase(FrozenModel):
    lead: TrioTrackId
    character: Literal["space", "answer", "build", "resolve"]


HarmonicTechnique = Literal["color", "inversion", "dominant", "borrowed", "deceptive", "tritone"]


class ImprovisationMove(FrozenModel):
    position: int = Field(ge=0, lt=CHORUS_BARS)
    technique: HarmonicTechnique
    chord: str


class ImprovisationPlan(FrozenModel):
    progression: tuple[str, ...] = Field(min_length=CHORUS_BARS, max_length=CHORUS_BARS)
    phrases: tuple[ImprovisationPhrase, ...] = Field(min_length=4, max_length=4)
    harmonic_moves: tuple[ImprovisationMove, ...] = Field(default=(), max_length=CHORUS_BARS)
    relative_to_start: bool = False

    def chord_for_bar(self, bar: int, start_bar: int) -> str:
        index = bar - start_bar if self.relative_to_start else bar
        return self.progression[index % CHORUS_BARS]

    @field_validator("progression")
    @classmethod
    def canonicalize(cls, progression: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(parse_chord(chord).symbol for chord in progression)


SectionRole = Literal["intro", "a", "b", "bridge", "outro"]
MELODY_STEPS_PER_QUARTER = 24
Motif = Literal["rise", "fall", "arch", "repeat"]
Texture = Literal["sparse", "full", "answer"]
KeysTone = Literal["piano", "electric", "vibes", "organ"]
KEYS_PROGRAMS: dict[KeysTone, int] = {"piano": 0, "electric": 4, "vibes": 11, "organ": 19}


class SongSection(FrozenModel):
    role: SectionRole
    bars: Literal[4, 8]
    bpm: int = Field(ge=40, le=240, strict=True)
    meter: TimeSignature
    progression: tuple[str, ...] = Field(min_length=4, max_length=8)
    motif: Motif
    texture: Texture
    intensity: int = Field(ge=0, le=100, strict=True)
    keys_tone: KeysTone = "electric"
    mix: Literal["lead", "rhythm", "balanced"] = "balanced"
    # Bars of (onset, duration, MIDI pitch, velocity), or None for rule playback.
    lead: tuple[tuple[tuple[int, int, int, int], ...] | None, ...] = ()

    @model_validator(mode="after")
    def normalize_chords(self) -> Self:
        if len(self.progression) != self.bars:
            raise ValueError("Expected one chord per section bar")
        canonical = tuple(parse_chord(chord).symbol for chord in self.progression)
        object.__setattr__(self, "progression", canonical)
        if self.lead:
            if len(self.lead) != self.bars:
                raise ValueError("Expected one lead entry per section bar")
            beats, denominator = TIME_SIGNATURES[self.meter]
            limit = MELODY_STEPS_PER_QUARTER * beats * 4 // denominator
            for bar in self.lead:
                if bar is None:
                    continue
                if len(bar) > 4:
                    raise ValueError("Expected at most four lead notes per bar")
                end = 0
                for start, duration, pitch, velocity in bar:
                    if (
                        not end <= start < start + duration <= limit
                        or not 55 <= pitch <= 88
                        or not 1 <= velocity <= 127
                    ):
                        raise ValueError("Invalid lead note for this section")
                    end = start + duration
        return self


class SongPlan(FrozenModel):
    tonic: str = Field(pattern=r"^[A-G][#b]?$")
    mode: Mode
    sections: tuple[SongSection, ...] = Field(min_length=3, max_length=7)
    base_intensity: int | None = Field(default=None, ge=0, le=100)

    @property
    def total_bars(self) -> int:
        return sum(section.bars for section in self.sections)

    def section_at(self, offset: int) -> tuple[int, int, SongSection]:
        start = 0
        position = offset % self.total_bars
        for index, section in enumerate(self.sections):
            if position < start + section.bars:
                return index, start, section
            start += section.bars
        raise AssertionError("Song must contain at least one section")


def section_volume(section: SongSection, track: TrackId, volume: float) -> float:
    if section.texture == "sparse" and track in {"drums", "rhythm_guitar", "synth"}:
        return volume * 0.72
    if section.mix == "lead" and track in {"drums", "bass", "rhythm_guitar", "synth"}:
        return volume * 0.83
    if section.mix == "rhythm" and track in {"piano", "lead_guitar", "synth"}:
        return volume * 0.83
    return volume


class MusicSettings(FrozenModel):
    bpm: int = Field(default=120, ge=40, le=240)
    density: float = Field(default=0.55, ge=0, le=1)
    progression: tuple[str, ...] = Field(
        default=DEFAULT_PROGRESSION,
        min_length=CHORUS_BARS,
        max_length=CHORUS_BARS,
    )
    loop: bool = True
    seed: int = Field(default=42, ge=-(2**53 - 1), le=2**53 - 1)
    conductor: ConductorPlan = ConductorPlan()
    collaboration: Collaboration = "negotiate"
    lineup: Lineup = "trio"
    meter: TimeSignature = "4/4"
    song: SongPlan | None = Field(default=None, exclude_if=lambda song: song is None)

    @property
    def meter_parts(self) -> tuple[int, int]:
        return TIME_SIGNATURES[self.meter]

    @property
    def bar_ticks(self) -> int:
        numerator, denominator = self.meter_parts
        return numerator * PPQ * 4 // denominator

    @property
    def pulse_ticks(self) -> int:
        return PPQ * 3 // 2 if self.meter == "6/8" else PPQ

    @field_validator("progression")
    @classmethod
    def normalize_progression(cls, progression: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(parse_chord(chord).symbol for chord in progression)

    def chord_at(self, bar: int) -> Chord:
        return parse_chord(self.progression[bar % CHORUS_BARS])

    @property
    def cinematic(self) -> bool:
        return self.conductor.profile is not None and self.conductor.profile.genre == "cinematic"

    @property
    def genre(self) -> Genre:
        return self.conductor.profile.genre if self.conductor.profile else self.conductor.style

    def score_keys_program(self, bar: int) -> int | None:
        if self.song is not None:
            return KEYS_PROGRAMS[self.song.section_at(bar)[2].keys_tone]
        return KEYS_PROGRAMS["organ"] if self.cinematic else None


class TrackSettings(FrozenModel):
    volume: float = Field(default=0.8, ge=0, le=1)
    muted: bool = False
    program: int = Field(default=0, ge=0, le=127)


Articulation = Literal["normal", "palm_mute", "legato", "harmonic"]


class Note(FrozenModel):
    model_config = ConfigDict(revalidate_instances="always")

    event_id: str = Field(min_length=1, max_length=100)
    track_id: TrackId
    bar: int = Field(ge=0)
    start_tick: int = Field(ge=0, lt=BAR_TICKS)
    duration_ticks: int = Field(gt=0, le=BAR_TICKS)
    pitch: int = Field(ge=0, le=127)
    velocity: int = Field(ge=1, le=127)
    channel: int = Field(ge=0, le=15)
    articulation: Articulation = Field(default="normal", exclude_if=lambda value: value == "normal")

    @model_validator(mode="after")
    def validate_track_and_extent(self) -> Self:
        track = TRACK_DEFINITIONS[self.track_id]
        if self.channel != track.channel:
            raise ValueError("Note channel does not match the assigned track")
        if not track.min_pitch <= self.pitch <= track.max_pitch:
            raise ValueError("Note pitch is outside the track range")
        if self.start_tick + self.duration_ticks > BAR_TICKS:
            raise ValueError("Note crosses the end of the bar")
        return self


class AgentDecision(FrozenModel):
    track_id: TrackId
    intent: str = Field(min_length=1, max_length=120)
    status: Literal["ready", "fallback", "timeout", "invalid", "failed", "busy"]
    detail: str | None = None


class NegotiationEvent(FrozenModel):
    proposal_id: str = Field(min_length=1, max_length=100)
    stage: Literal["proposal", "accepted", "declined", "timeout"]
    sender: TrackId
    recipient: TrackId
    kind: Literal["kick_pattern", "reserve_space", "lighter_drums"]
    ticks: tuple[int, ...] = Field(default=(), max_length=16)
    detail: str = Field(min_length=1, max_length=180)

    @model_validator(mode="after")
    def validate_route_and_ticks(self) -> Self:
        route = {
            "kick_pattern": ("drums", "bass"),
            "reserve_space": ("bass", "piano"),
            "lighter_drums": ("piano", "drums"),
        }[self.kind]
        if (self.sender, self.recipient) != route:
            raise ValueError("Negotiation offer has the wrong instrument roles")
        if tuple(sorted(set(self.ticks))) != self.ticks or any(
            not 0 <= tick < BAR_TICKS for tick in self.ticks
        ):
            raise ValueError("Negotiation ticks must be unique, ordered, and in the bar")
        return self


class GrooveTransition(FrozenModel):
    phase: Literal["pickup", "arrival"]
    from_style: Style
    to_style: Style
    from_bpm: int = Field(ge=40, le=240)
    to_bpm: int = Field(ge=40, le=240)
    from_meter: TimeSignature = "4/4"
    to_meter: TimeSignature = "4/4"
    next_chord: str | None = None
    from_genre: Genre | None = Field(default=None, exclude_if=lambda value: value is None)
    to_genre: Genre | None = Field(default=None, exclude_if=lambda value: value is None)
    from_groove: Groove | None = Field(default=None, exclude_if=lambda value: value is None)
    to_groove: Groove | None = Field(default=None, exclude_if=lambda value: value is None)


class PlayerPhrase(FrozenModel):
    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    pitch_register: Literal["low", "middle", "high"] = Field(alias="register")
    activity: Literal["sparse", "busy"]
    accent_tick: int | None = Field(default=None, ge=0, lt=BAR_TICKS)
    velocity: Literal["soft", "strong"] = "soft"


class PlayerResponse(FrozenModel):
    phrase: PlayerPhrase
    drums: str = Field(max_length=180)
    bass: str = Field(max_length=180)
    piano: str = Field(max_length=180)


def validate_notes(bar: int, notes: tuple[Note, ...]) -> tuple[Note, ...]:
    """Validate ownership by bar, capacity, uniqueness and overlap as one unit."""
    ordered = tuple(
        sorted(
            notes,
            key=lambda note: (note.start_tick, note.channel, note.pitch, note.event_id),
        )
    )
    counts: Counter[str] = Counter()
    event_ids: set[str] = set()
    active_until: dict[tuple[int, int], int] = {}
    for note in ordered:
        if note.bar != bar:
            raise ValueError("Note belongs to a different bar")
        if note.event_id in event_ids:
            raise ValueError(f"Duplicate note event ID '{note.event_id}'")
        event_ids.add(note.event_id)
        counts[note.track_id] += 1
        if counts[note.track_id] > TRACK_DEFINITIONS[note.track_id].max_notes:
            raise ValueError(f"Too many notes on track '{note.track_id}'")
        voice = (note.channel, note.pitch)
        if active_until.get(voice, 0) > note.start_tick:
            raise ValueError(f"Overlapping pitch {note.pitch} on track '{note.track_id}'")
        active_until[voice] = note.start_tick + note.duration_ticks
    return ordered


class BarPlan(FrozenModel):
    bar: int = Field(ge=0)
    version: int = Field(ge=0)
    revision: int = Field(default=0, ge=0)
    notes: tuple[Note, ...]
    fallback_tracks: tuple[TrackId, ...] = ()
    intents: tuple[AgentDecision, ...] = ()
    negotiation: tuple[NegotiationEvent, ...] = ()
    transition: GrooveTransition | None = None
    player_response: PlayerResponse | None = None
    harmony: str | None = None
    improvisation_id: int | None = None
    improvisation_phrase: ImprovisationPhrase | None = None
    time_signature: TimeSignature | None = None
    bpm: int | None = Field(default=None, ge=40, le=240)

    @field_validator("notes")
    @classmethod
    def validate_bar_notes(cls, notes: tuple[Note, ...], info: ValidationInfo) -> tuple[Note, ...]:
        # A malformed bar already has its own field error.
        return validate_notes(info.data["bar"], notes) if "bar" in info.data else notes

    @model_validator(mode="after")
    def validate_decisions(self) -> Self:
        decision_tracks = tuple(decision.track_id for decision in self.intents)
        if len(set(decision_tracks)) != len(decision_tracks):
            raise ValueError("Each track can make one decision per bar")
        if len(set(self.fallback_tracks)) != len(self.fallback_tracks):
            raise ValueError("Fallback tracks must be unique")
        if self.intents:
            fallback = {
                decision.track_id for decision in self.intents if decision.status != "ready"
            }
            if fallback != set(self.fallback_tracks):
                raise ValueError("Fallback statuses must match the fallback tracks")
        return self
