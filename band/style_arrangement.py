"""Let the conductor model arrange a chosen style; rules keep every choice playable.

A style chip first applies the rule preset at once. The model then proposes a concrete
arrangement from what it knows about the style and the current context: groove, key
and harmony, tempo, intensity, density, each player's role and the band's tones, plus
a one-line reason. ``materialize`` checks each field against the style's allowed
values and ranges before anything reaches the band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import Field

from band.harmony import materialize_harmony
from band.music import (
    ConductorPlan,
    FrozenModel,
    Genre,
    Groove,
    Harmony,
    Mode,
    MusicSettings,
    TrackId,
)
from band.style_direction import (
    _ALTERNATE_GROOVES,
    _DEFAULTS,
    StyleIntent,
    arrange_style,
)

ARRANGE_PROMPT_VERSION = "genre-arrangement-v1"
ARRANGE_PROMPT = """You arrange an original instrumental band in the requested style.
Use what you know about how that style is really played: its usual groove, key and
chord habits, tempo, energy, what the drums, bass and keyboard do, and the guitar and
keyboard sounds. Choose only allowed values. Start from the typical recipe but adapt
it to the current context; keep the current chords (harmony "keep") when they already
suit the style. bpm must lie inside bpm_range. note is one short Chinese sentence
(at most 40 characters) saying why this arrangement fits the style.
Keep intensity and density inside intensity_range and density_range, and pick
guitar sounds from the listed options. Roles: drums balanced/sparse/driving; bass
walking/root/syncopated; keys comp/sustained/syncopated.
Example for hard_rock: {"groove":"straight_eighth","harmony":"minor_1767","mode":"minor",
"tonic":"E","bpm":138,"intensity":86,"density":0.78,"drums":"driving","bass":"root",
"keys":"sustained","rhythm_guitar":"distorted","lead_guitar":"distorted",
"keys_tone":"organ","pad":"strings","note":"失真强力和弦推八分，管风琴长音托底"}.
Output JSON only."""

TONICS = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
TEMPO: dict[Genre, tuple[int, int]] = {
    "swing": (100, 200),
    "bossa": (100, 150),
    "funk": (90, 120),
    "ballad": (60, 90),
    "rock": (100, 150),
    "hard_rock": (110, 170),
    "alternative_rock": (90, 140),
    "pop": (90, 130),
    "blues": (60, 120),
    "reggae": (70, 100),
    "disco": (110, 130),
    "soul": (70, 110),
    "cinematic": (60, 100),
    "ambient": (60, 90),
    "electronic": (115, 135),
    "latin": (90, 140),
    "country": (90, 140),
    "hip_hop": (80, 100),
    "classical": (60, 120),
}
# Per-style guardrails: (intensity, density) ranges and the guitar sounds that fit.
_ROCK = ((70, 95), (0.6, 0.9), ("overdriven", "distorted"), ("distorted", "overdriven"))
_CLEAN = ((25, 65), (0.3, 0.7), ("clean", "acoustic"), ("clean",))
_GROOVE = ((55, 85), (0.5, 0.85), ("clean", "overdriven"), ("clean", "overdriven"))
_SOFT = ((15, 50), (0.2, 0.5), ("clean", "acoustic"), ("clean",))
GUIDES: dict[
    Genre, tuple[tuple[int, int], tuple[float, float], tuple[str, ...], tuple[str, ...]]
] = {
    "rock": (
        (60, 90),
        (0.55, 0.85),
        ("overdriven", "distorted", "clean"),
        ("overdriven", "distorted"),
    ),
    "hard_rock": _ROCK,
    "alternative_rock": (
        (50, 85),
        (0.45, 0.8),
        ("overdriven", "distorted", "clean"),
        ("overdriven", "clean", "distorted"),
    ),
    "blues": ((40, 75), (0.4, 0.75), ("clean", "overdriven"), ("overdriven", "clean")),
    "country": ((40, 70), (0.4, 0.7), ("acoustic", "clean"), ("clean",)),
    "pop": ((40, 75), (0.4, 0.75), ("clean", "acoustic", "overdriven"), ("clean", "overdriven")),
    "funk": _GROOVE,
    "disco": _GROOVE,
    "soul": ((40, 70), (0.4, 0.7), ("clean",), ("clean",)),
    "reggae": ((40, 65), (0.35, 0.6), ("clean",), ("clean",)),
    "hip_hop": ((45, 75), (0.35, 0.65), ("clean",), ("clean",)),
    "electronic": ((55, 85), (0.5, 0.85), ("clean", "overdriven"), ("clean", "distorted")),
    "swing": _CLEAN,
    "bossa": _CLEAN,
    "latin": ((45, 75), (0.45, 0.75), ("clean", "acoustic"), ("clean",)),
    "ballad": _SOFT,
    "ambient": _SOFT,
    "cinematic": ((25, 70), (0.2, 0.6), ("clean", "acoustic"), ("clean",)),
    "classical": _SOFT,
}
RHYTHM_TONES = {"clean": 27, "overdriven": 29, "distorted": 30, "acoustic": 25}
LEAD_TONES = {"distorted": 30, "overdriven": 29, "clean": 27}
KEYS_TONES = {"piano": 0, "bright": 1, "electric": 4, "organ": 19, "vibes": 11}
PAD_TONES = {"pad": 89, "strings": 48, "accordion": 21, "brass": 61}


class ArrangementChoice(FrozenModel):
    groove: Groove
    harmony: Harmony
    mode: Mode
    tonic: str
    bpm: int
    intensity: int = Field(ge=0, le=100)
    density: float = Field(ge=0, le=1)
    drums: Literal["balanced", "sparse", "driving"]
    bass: Literal["walking", "root", "syncopated"]
    keys: Literal["comp", "sustained", "syncopated"]
    rhythm_guitar: Literal["clean", "overdriven", "distorted", "acoustic"]
    lead_guitar: Literal["distorted", "overdriven", "clean"]
    keys_tone: Literal["piano", "bright", "electric", "organ", "vibes"]
    pad: Literal["pad", "strings", "accordion", "brass"]
    note: str = Field(max_length=120)

    @classmethod
    def output_schema(cls) -> dict:
        schema = cls.model_json_schema()
        for field, values in (("groove", Groove), ("harmony", Harmony), ("mode", Mode)):
            schema["properties"][field] = {"type": "string", "enum": list(get_args(values))}
        schema["properties"]["tonic"] = {"type": "string", "enum": list(TONICS)}
        return schema


def context_for(genre: Genre, settings: MusicSettings) -> dict:
    defaults = _DEFAULTS[genre]
    return {
        "style": genre,
        "typical_recipe": {
            "groove": defaults.groove,
            "other_grooves": list(_ALTERNATE_GROOVES.get(genre, ())),
            "harmony": defaults.harmony,
            "drums": defaults.drums,
            "bass": defaults.bass,
            "keys": defaults.piano,
        },
        "bpm_range": list(TEMPO[genre]),
        "intensity_range": list(GUIDES[genre][0]),
        "density_range": list(GUIDES[genre][1]),
        "rhythm_guitar_options": list(GUIDES[genre][2]),
        "lead_guitar_options": list(GUIDES[genre][3]),
        "current": {
            "bpm": settings.bpm,
            "meter": settings.meter,
            "chords": list(settings.progression),
            "lineup": settings.lineup,
            "density": settings.density,
        },
    }


GROOVE_LABELS = {
    "straight_eighth": "平直八分",
    "shuffle": "三连音律动",
    "one_drop": "雷鬼 one-drop",
    "four_on_floor": "四拍底鼓",
    "sixteenth": "十六分切分",
    "half_time": "半拍律动",
    "bossa": "波萨诺瓦切分",
    "swing": "摇摆八分",
}
TONE_LABELS = {"clean": "清音", "overdriven": "过载", "distorted": "失真", "acoustic": "原声"}


@dataclass(frozen=True)
class GenreArrangement:
    plan: ConductorPlan
    progression: tuple[str, ...] | None
    bpm: int
    density: float
    tracks: dict[TrackId, dict]
    note: str
    corrections: tuple[str, ...]


def materialize(
    genre: Genre, choice: ArrangementChoice, settings: MusicSettings
) -> GenreArrangement:
    """Turn the model's choice into band settings, correcting anything out of bounds."""
    corrections = []
    defaults = _DEFAULTS[genre]
    grooves = (defaults.groove, *_ALTERNATE_GROOVES.get(genre, ()))
    groove = choice.groove
    if groove not in grooves:
        corrections.append(f"律动 {groove} 不属于该风格，改用 {defaults.groove}")
        groove = defaults.groove
    tonic = choice.tonic if choice.tonic in TONICS else "C"
    keep = choice.harmony == "keep"
    intent = StyleIntent(
        genre=genre,
        groove=groove,
        mode=None if keep else choice.mode,
        harmony=None if keep else choice.harmony,
        tonic=None if keep else tonic,
        reference=None,
        curated=None,
        preserve_harmony=keep,
        change_harmony=not keep,
        rich=True,
    )
    plan, harmony = arrange_style(intent, settings.conductor, None)
    progression = materialize_harmony(plan.profile) if harmony != "keep" and plan.profile else None
    low, high = TEMPO[genre]
    bpm = min(high, max(low, choice.bpm))
    if bpm != choice.bpm:
        corrections.append(f"速度 {choice.bpm} 超出 {low}–{high}，改为 {bpm}")
    (low_i, high_i), (low_d, high_d), rhythm_options, lead_options = GUIDES[genre]
    intensity = min(high_i, max(low_i, choice.intensity))
    if intensity != choice.intensity:
        corrections.append(
            f"强度 {choice.intensity}% 超出该风格的 {low_i}–{high_i}%，改为 {intensity}%"
        )
    density = round(min(high_d, max(low_d, choice.density)), 2)
    if density != round(choice.density, 2):
        corrections.append(f"密度 {choice.density:.2f} 改为 {density:.2f}")
    rhythm = choice.rhythm_guitar if choice.rhythm_guitar in rhythm_options else rhythm_options[0]
    lead = choice.lead_guitar if choice.lead_guitar in lead_options else lead_options[0]
    if (rhythm, lead) != (choice.rhythm_guitar, choice.lead_guitar):
        corrections.append(f"吉他音色改为 {rhythm} / {lead}")
    plan = plan.model_copy(
        update={
            "intensity": intensity,
            "drums": choice.drums,
            "bass": choice.bass,
            "piano": choice.keys,
        }
    )
    tracks: dict[TrackId, dict] = {
        "piano": {"program": KEYS_TONES[choice.keys_tone]},
        "rhythm_guitar": {"program": RHYTHM_TONES[rhythm]},
        "lead_guitar": {"program": LEAD_TONES[lead]},
        "synth": {"program": PAD_TONES[choice.pad]},
    }
    note = choice.note.strip()[:40]
    if sum("\u4e00" <= char <= "\u9fff" for char in note) < 4:
        # The model's reason was not a real sentence; describe what was actually chosen.
        note = "，".join(
            (
                GROOVE_LABELS.get(plan.profile.groove if plan.profile else "", "按风格律动"),
                f"{TONE_LABELS[rhythm]}节奏吉他",
                f"强度 {intensity}%",
            )
        )
    return GenreArrangement(
        plan=plan,
        progression=progression,
        bpm=bpm,
        density=density,
        tracks=tracks,
        note=note,
        corrections=tuple(corrections),
    )
