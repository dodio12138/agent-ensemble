"""The game-score band: what each game style asks of strings, brass, theme and chip parts.

Game music leans on a few recognisable recipes: a hummable theme that comes back every
phrase, layers that drop in and out by scene, and sounds that name the genre at once
(square waves for 8-bit, a flute over harp for an RPG town, driving strings and brass
for a boss fight). The rule musicians read their part's role from here, and choosing a
style on the game lineup stages the matching sounds and balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from band.music import Genre, TrackId

GAME_GENRES: tuple[Genre, ...] = (
    "chiptune",
    "platformer",
    "rpg",
    "battle",
    "exploration",
    "stealth",
)

StringsRole = Literal["pad", "ostinato", "pizzicato", "rest"]
BrassRole = Literal["stabs", "fanfare", "swell", "rest"]
ThemeRhythm = Literal["bouncy", "lyrical", "heroic", "sparse", "creeping"]
ChipRole = Literal["arp", "sparkle", "rest"]


@dataclass(frozen=True, slots=True)
class GameParts:
    strings: StringsRole
    brass: BrassRole
    theme: ThemeRhythm
    chip: ChipRole
    theme_center: int


GAME_PARTS: dict[Genre, GameParts] = {
    "chiptune": GameParts("rest", "rest", "bouncy", "arp", 79),
    "platformer": GameParts("pad", "stabs", "bouncy", "arp", 76),
    "rpg": GameParts("pad", "fanfare", "lyrical", "sparkle", 74),
    "battle": GameParts("ostinato", "stabs", "heroic", "rest", 72),
    "exploration": GameParts("pad", "swell", "sparse", "sparkle", 76),
    "stealth": GameParts("pizzicato", "swell", "creeping", "sparkle", 70),
}


def game_parts(genre: Genre, intensity: int) -> GameParts:
    """Game styles have fixed roles; any other style gets an orchestral reading of it."""
    if genre in GAME_PARTS:
        return GAME_PARTS[genre]
    return GameParts(
        "ostinato" if intensity >= 75 else "pad",
        "stabs" if intensity >= 65 else "swell",
        "lyrical",
        "sparkle",
        74,
    )


# General MIDI program and level for each part; None mutes the part for that style.
Tone = tuple[int, float | None]
_ORCHESTRAL: dict[TrackId, Tone] = {
    "piano": (46, 0.6),  # harp
    "bass": (32, 0.68),
    "strings": (48, 0.55),
    "brass": (60, 0.5),  # french horns
    "melody": (73, 0.7),  # flute
    "chip": (8, 0.38),  # celesta
}
GAME_TONES: dict[Genre, dict[TrackId, Tone]] = {
    "chiptune": {
        "piano": (80, 0.38),
        "bass": (38, 0.7),
        "strings": (48, None),
        "brass": (61, None),
        "melody": (80, 0.6),
        "chip": (80, 0.42),
    },
    "platformer": {
        "piano": (80, 0.36),
        "bass": (38, 0.7),
        "strings": (50, 0.4),
        "brass": (62, 0.48),
        "melody": (80, 0.6),
        "chip": (80, 0.4),
    },
    "rpg": _ORCHESTRAL,
    "battle": {
        "piano": (0, 0.52),
        "bass": (33, 0.75),
        "strings": (48, 0.66),
        "brass": (61, 0.62),
        "melody": (60, 0.72),
        "chip": (8, None),
    },
    "exploration": {
        "piano": (46, 0.5),
        "bass": (32, 0.55),
        "strings": (49, 0.55),
        "brass": (60, 0.4),
        "melody": (75, 0.62),  # pan flute
        "chip": (8, 0.36),
    },
    "stealth": {
        "piano": (0, 0.45),
        "bass": (32, 0.6),
        "strings": (45, 0.55),  # pizzicato
        "brass": (60, 0.36),
        "melody": (71, 0.58),  # clarinet
        "chip": (11, 0.34),  # vibraphone
    },
}


def game_tones(genre: Genre) -> dict[TrackId, dict]:
    """Track patches that give a style its game sound on the game lineup."""
    tones = GAME_TONES.get(genre, _ORCHESTRAL)
    return {
        track: {"program": program, "muted": volume is None}
        | ({} if volume is None else {"volume": volume})
        for track, (program, volume) in tones.items()
    }
