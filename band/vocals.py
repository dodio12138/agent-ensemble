"""A wordless voice that joins every lineup: choir pads, a sung line, backing oohs or scat.

The voice sings vowels, not words, so it plays along live like any other musician.
Each style has a usual way to use it, which the rules fall back on, and a short list
of ways the conductor model may choose from. VOCAL_GUIDE is what the model is told.
"""

from __future__ import annotations

from band.music import Genre, Lineup, MusicSettings, VocalRole

# What a band in each style would usually do with a singer who has no lyrics.
VOCAL_DEFAULTS: dict[Genre, VocalRole] = {
    "swing": "scat",
    "bossa": "scat",
    "funk": "backing",
    "ballad": "lead",
    "rock": "lead",
    "hard_rock": "backing",
    "alternative_rock": "lead",
    "pop": "lead",
    "blues": "lead",
    "reggae": "backing",
    "disco": "backing",
    "soul": "lead",
    "cinematic": "pad",
    "ambient": "pad",
    "electronic": "pad",
    "latin": "backing",
    "country": "lead",
    "hip_hop": "backing",
    "classical": "pad",
    "chiptune": "off",
    "platformer": "off",
    "rpg": "pad",
    "battle": "pad",
    "exploration": "pad",
    "stealth": "off",
}
_SINGER = ("lead", "backing", "pad", "off")
_GROOVE = ("backing", "lead", "off")
_SCORE = ("pad", "off")
# The choices that suit each style; the model must pick one of these.
VOCAL_OPTIONS: dict[Genre, tuple[VocalRole, ...]] = {
    "swing": ("scat", "backing", "off"),
    "bossa": ("scat", "backing", "off"),
    "funk": _GROOVE,
    "ballad": _SINGER,
    "rock": _SINGER,
    "hard_rock": _GROOVE,
    "alternative_rock": _SINGER,
    "pop": _SINGER,
    "blues": ("lead", "backing", "scat", "off"),
    "reggae": _GROOVE,
    "disco": _GROOVE,
    "soul": _SINGER,
    "cinematic": _SCORE,
    "ambient": ("pad", "lead", "off"),
    "electronic": ("pad", "backing", "off"),
    "latin": ("backing", "scat", "off"),
    "country": _SINGER,
    "hip_hop": ("backing", "off"),
    "classical": _SCORE,
    "chiptune": ("off",),
    "platformer": ("off", "backing"),
    "rpg": ("pad", "lead", "off"),
    "battle": _SCORE,
    "exploration": ("pad", "lead", "off"),
    "stealth": ("off", "pad"),
}
# General MIDI: 52 Choir Aahs, 53 Voice Oohs, 54 Synth Voice.
VOCAL_PROGRAMS: dict[VocalRole, int] = {
    "pad": 52,
    "lead": 53,
    "backing": 53,
    "scat": 53,
    "off": 53,
}

VOCAL_GUIDE = """vocal is a wordless voice (vowels only, no lyrics) that sings with the band:
- pad: a choir holding "aah" chord tones under everything. Film, classical, ambient,
  epic battles, RPG towns and open-world exploration.
- lead: a sung "ooh" melody in four-bar phrases with breaths; every other phrase it
  steps back to backing so the band can answer. Pop, soul, ballads, rock, country, blues.
- backing: short "ooh" harmonies (third and fifth) answering at the end of every
  second bar. Funk, disco, reggae, latin, hip hop and driving hard rock.
- scat: a jazz scat line on swung eighths. Swing and bossa nova, sometimes blues.
- off: no voice. 8-bit chiptune, stealth, or when the music should stay instrumental.
On the game lineup the theme instrument already carries the tune, so prefer pad.
Pick vocal from vocal_options only."""


def vocal_options(genre: Genre, lineup: Lineup) -> tuple[VocalRole, ...]:
    options = VOCAL_OPTIONS[genre]
    if lineup == "game":
        # The theme instrument sings the melody; a second lead would fight it.
        options = tuple(role for role in options if role not in {"lead", "scat"}) or ("pad",)
    return options


def default_vocal(genre: Genre, lineup: Lineup) -> VocalRole:
    role = VOCAL_DEFAULTS[genre]
    return role if role in vocal_options(genre, lineup) else vocal_options(genre, lineup)[0]


def vocal_role(settings: MusicSettings) -> VocalRole:
    """The role asked for by the conductor, or the style's usual one."""
    return settings.conductor.vocal or default_vocal(settings.genre, settings.lineup)


def vocal_program(role: VocalRole, genre: Genre) -> int:
    if role == "pad" and genre == "electronic":
        return 54  # synth voice
    return VOCAL_PROGRAMS[role]
