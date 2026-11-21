"""Bounded song choices and per-bar projection for the live arranger."""

import re
from dataclasses import replace
from typing import Literal

from pydantic import Field

from band.harmony import materialize_harmony
from band.music import (
    CHORUS_BARS,
    HARMONY_MODES,
    ConductorPlan,
    FrozenModel,
    Genre,
    Groove,
    Harmony,
    KeysTone,
    Mode,
    Motif,
    MusicProfile,
    MusicSettings,
    SongPlan,
    SongSection,
)
from band.style_direction import StyleChoice, arrange_style, style_intent
from band.timing_intent import timing_intent

SONG_PROMPT = """Plan an original instrumental song for the user's ensemble.
Choose form compact (intro A B A outro) or bridge (intro A B bridge B outro).
Choose a four or eight bar body phrase. Set contour of the main motif and a B
contrast (rise, fall, arch, repeat). Pick compatible chord formulas from recipes.
Texture B may be full or answer. Tempo is quarter-note BPM. Main tempo/meter default
to the current values unless the instruction asks for a change or delegates timing.
Use contrast_bpm 0 and contrast_meter keep unless the instruction asks for a B
tempo/meter change, or explicitly lets you choose timing for individual sections.
A repeated section should sound like the same song with a familiar motif. Favor
space in intro/outro. Keep main and contrast harmonies in the same mode.
Choose keys_tone (piano, electric, vibes, organ) to match the style.
Choose genre from the allowed values if useful. For mixing choose lead (melody
in front), rhythm (more weight from drums and bass), or balanced.
Choose a groove independently of genre for hybrid styles or use keep.
Return JSON only."""


class SongChoice(FrozenModel):
    form: Literal["compact", "bridge"]
    genre: Literal[Genre, "keep"]
    groove: Literal[Groove, "keep"]
    body_bars: Literal[4, 8]
    a_harmony: Harmony
    b_harmony: Harmony
    a_motif: Motif
    b_motif: Motif
    b_texture: Literal["full", "answer"]
    keys_tone: KeysTone
    mix: Literal["lead", "rhythm", "balanced"]
    bpm: int = Field(ge=0, le=240, strict=True)
    meter: Literal["keep", "4/4", "3/4", "6/8"]
    contrast_bpm: int = Field(ge=0, le=240, strict=True)
    contrast_meter: Literal["keep", "4/4", "3/4", "6/8"]


_CONTRAST = {
    "major": ("pop_1564", "major_145", "soul_251"),
    "minor": ("minor_1767", "minor_1645"),
    "dorian": ("dorian_vamp",),
    "mixolydian": ("mixolydian_174",),
}
_TIMING_TARGETS = {
    "a": re.compile(r"主歌|\bA\s*[段部]|\bverse\b", re.IGNORECASE),
    "b": re.compile(r"副歌|\bB\s*[段部]|\b(?:chorus|refrain)\b", re.IGNORECASE),
    "bridge": re.compile(r"桥段|橋段|\bbridge\b", re.IGNORECASE),
    "intro": re.compile(r"前奏|序奏|\bintro\b", re.IGNORECASE),
    "outro": re.compile(r"尾声|尾聲|\boutro\b", re.IGNORECASE),
}
_BRIDGE = re.compile(r"桥段|橋段|\bbridge\b", re.IGNORECASE)
_TIMING_PERMISSION = re.compile(
    r"(?:速度|拍号|拍號|[Bb][Pp][Mm])[^，。；]{0,9}(?:由你|自行|按需|酌情|根据|可变|變化)"
    r"|(?:自行|按需|酌情)[^，。；]{0,9}(?:调速|变速|變速|改拍|换拍|換拍)"
    r"|\b(?:choose|vary|change) (?:the )?(?:tempo|meter) (?:as needed|by section)\b"
)
_BPM_WORD = re.compile(r"速度|曲速|\b(?:tempo|bpm)\b", re.IGNORECASE)
_METER_WORD = re.compile(r"拍号|拍號|拍子|\b(?:meter|time signature)\b", re.IGNORECASE)
_FIXED_TIMING = re.compile(r"保持|保留|不变|不變|不要改|\bkeep\b|\bunchanged\b", re.IGNORECASE)
_LEAD_MIX = re.compile(
    r"突出旋律|旋律突出|旋律在前|\b(?:foreground|feature) the melody\b", re.IGNORECASE
)
_RHYTHM_MIX = re.compile(
    r"突出(?:鼓和贝斯|鼓贝斯|节奏组)|鼓贝斯突出|\bforeground (?:the )?rhythm section\b",
    re.IGNORECASE,
)


def song_context(current: MusicSettings, instruction: str) -> dict:
    return {
        "instruction": instruction,
        "bpm": current.bpm,
        "meter": current.meter,
        "genre": current.conductor.profile.genre
        if current.conductor.profile
        else current.conductor.style,
        "recipes": HARMONY_MODES,
    }


def _timing(current: MusicSettings, instruction: str):
    clauses: dict[str, list[str]] = {"base": [], **{target: [] for target in _TIMING_TARGETS}}
    for clause in re.split(r"[，。；,;\n]", instruction):
        target = next(
            (name for name, pattern in _TIMING_TARGETS.items() if pattern.search(clause)), "base"
        )
        cleaned = _TIMING_TARGETS[target].sub("", clause) if target != "base" else clause
        clauses[target].append(cleaned)
    return {name: timing_intent("，".join(text), current) for name, text in clauses.items()}


def _may_vary(instruction: str, axis: re.Pattern[str]) -> bool:
    clauses = re.split(r"[，。；,;\n]", instruction)
    if any(axis.search(clause) and _FIXED_TIMING.search(clause) for clause in clauses):
        return False
    return any(axis.search(clause) and _TIMING_PERMISSION.search(clause) for clause in clauses)


def _recipe(
    formula: Harmony,
    mode: Mode,
    profile: MusicProfile,
) -> tuple[str, ...]:
    return materialize_harmony(profile.model_copy(update={"harmony": formula, "mode": mode})) or ()


def build_song(
    current: MusicSettings, instruction: str, choice: SongChoice | None
) -> tuple[SongPlan, ConductorPlan]:
    style = style_intent(instruction)
    inferred = (
        StyleChoice(genre=choice.genre, groove=choice.groove, mode="keep", harmony="keep")
        if choice
        else None
    )
    conductor, chosen = arrange_style(replace(style, descriptive=True), current.conductor, inferred)
    profile = conductor.profile
    tonic = profile.tonic if profile else "C"
    mode: Mode = profile.mode if profile else "major"
    valid = _CONTRAST[mode]
    preferred = chosen if chosen != "keep" else (profile.harmony if profile else "soul_251")
    harmony_a = preferred if HARMONY_MODES.get(preferred) == mode else valid[0]
    if choice and not style.harmony and HARMONY_MODES.get(choice.a_harmony) == mode:
        harmony_a = choice.a_harmony
    alternatives = tuple(recipe for recipe in valid if recipe != harmony_a)
    harmony_b = (
        choice.b_harmony
        if choice and HARMONY_MODES.get(choice.b_harmony) == mode and choice.b_harmony != harmony_a
        else alternatives[0]
        if alternatives
        else harmony_a
    )
    if profile is None:
        profile = MusicProfile(
            genre=conductor.style,
            groove="swing",
            tonic=tonic,
            mode=mode,
            harmony=harmony_a,
            basis="theory",
        )
    a = _recipe(harmony_a, mode, profile)
    b = _recipe(harmony_b, mode, profile)
    if b == a:
        # A one-formula modal vamp can still shift the harmonic emphasis in B.
        b = b[2:] + b[:2] if b[0] != b[2] else b[1:] + b[:1]
    timing = _timing(current, instruction)
    main, verse_timing, contrast = timing["base"], timing["a"], timing["b"]
    vary_bpm = _may_vary(instruction, _BPM_WORD)
    vary_meter = _may_vary(instruction, _METER_WORD)
    bpm = (
        verse_timing.bpm
        or main.bpm
        or (choice.bpm if choice and vary_bpm and choice.bpm >= 40 else current.bpm)
    )
    meter = (
        verse_timing.meter
        or main.meter
        or (choice.meter if choice and vary_meter and choice.meter != "keep" else current.meter)
    )
    contrast_bpm = choice.contrast_bpm if choice and vary_bpm else 0
    contrast_meter = choice.contrast_meter if choice and vary_meter else "keep"
    b_bpm = contrast.bpm or (contrast_bpm if contrast_bpm >= 40 else bpm)
    b_meter = contrast.meter or (contrast_meter if contrast_meter != "keep" else meter)

    bars = choice.body_bars if choice else 8
    motif = choice.a_motif if choice else "arch"
    contrast_motif = choice.b_motif if choice else "rise"
    if contrast_motif == motif:
        contrast_motif = "fall" if motif == "rise" else "rise"
    home = tonic + ("m" if mode in {"minor", "dorian"} else "")
    energy = conductor.intensity
    default_tone: KeysTone = "electric" if conductor.style in {"funk", "bossa"} else "piano"
    keys_tone = choice.keys_tone if choice else default_tone
    if profile.genre == "cinematic":
        keys_tone = "organ"
        motif = "repeat"
        if contrast_motif == motif:
            contrast_motif = "rise"
    mix = choice.mix if choice else "balanced"
    if _LEAD_MIX.search(instruction):
        mix = "lead"
    elif _RHYTHM_MIX.search(instruction):
        mix = "rhythm"
    verse = SongSection(
        role="a",
        bars=bars,
        bpm=bpm,
        meter=meter,
        progression=a[:bars],
        motif=motif,
        texture="answer",
        intensity=energy,
        keys_tone=keys_tone,
        mix=mix,
    )
    chorus = SongSection(
        role="b",
        bars=bars,
        bpm=b_bpm,
        meter=b_meter,
        progression=b[:bars],
        motif=contrast_motif,
        texture=choice.b_texture if choice else "full",
        intensity=min(100, energy + 14),
        keys_tone=keys_tone,
        mix=mix,
    )
    intro = SongSection(
        role="intro",
        bars=4,
        bpm=timing["intro"].bpm or bpm,
        meter=timing["intro"].meter or meter,
        progression=(home, *a[1:4]),
        motif=motif,
        texture="sparse",
        intensity=max(0, energy - 16),
        keys_tone=keys_tone,
    )
    outro = SongSection(
        role="outro",
        bars=4,
        bpm=timing["outro"].bpm or bpm,
        meter=timing["outro"].meter or meter,
        progression=(*a[-3:], home),
        motif="fall",
        texture="sparse",
        intensity=max(0, energy - 13),
        keys_tone=keys_tone,
    )
    if (choice and choice.form == "bridge") or _BRIDGE.search(instruction):
        bridge = SongSection(
            role="bridge",
            bars=4,
            bpm=timing["bridge"].bpm or bpm,
            meter=timing["bridge"].meter or meter,
            progression=(b[2], b[3], a[1], a[3]),
            motif="repeat",
            texture="sparse",
            intensity=max(0, energy - 7),
            keys_tone=keys_tone,
        )
        sections = (intro, verse, chorus, bridge, chorus, outro)
    else:
        sections = (intro, verse, chorus, verse, outro)
    return SongPlan(tonic=tonic, mode=mode, sections=sections, base_intensity=energy), conductor


def project_song(
    settings: MusicSettings, origin: int, bar: int
) -> tuple[MusicSettings, SongSection | None, int]:
    song = settings.song
    if song is None:
        return settings, None, 0
    _, start, section = song.section_at(bar - origin)
    section_bar = (bar - origin) % song.total_bars - start
    first = (bar - section_bar) % CHORUS_BARS
    progression = section.progression * (CHORUS_BARS // section.bars)
    progression = progression[-first:] + progression[:-first] if first else progression
    base = settings.conductor
    sparse = section.texture == "sparse"
    strength = section.intensity
    if song.base_intensity is not None:
        strength = max(0, min(100, strength + base.intensity - song.base_intensity))
    plan = base.model_copy(
        update={
            "intensity": strength,
            "drums": "sparse" if sparse else "driving" if section.texture == "full" else base.drums,
            "piano": "sustained" if sparse else base.piano,
        }
    )
    return (
        settings.model_copy(
            update={
                "bpm": section.bpm,
                "meter": section.meter,
                "progression": progression,
                "conductor": plan,
                "density": min(settings.density, 0.40) if sparse else settings.density,
            }
        ),
        section,
        section_bar,
    )
