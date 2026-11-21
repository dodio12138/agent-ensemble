"""Transpose supported chord formulas into the session's eight-bar form."""

from band.music import Harmony, Mode, MusicProfile, parse_chord

_SCALES: dict[Mode, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
}

# Each pair encodes the one-based scale degree and chord quality, one chord per bar.
_FORMULAS: dict[Harmony, tuple[tuple[int, str], ...]] = {
    "pop_1564": ((1, ""), (5, ""), (6, "m"), (4, "")) * 2,
    "minor_1645": ((1, "m"), (6, ""), (4, "m"), (5, "")) * 2,
    "mixolydian_174": ((1, ""), (1, ""), (7, ""), (4, "")) * 2,
    "blues_8": ((1, "7"), (5, "7"), (4, "7"), (4, "7"), (1, "7"), (5, "7"), (1, "7"), (5, "7")),
    "dorian_vamp": ((1, "m7"), (4, "7")) * 4,
    "minor_1767": ((1, "m"), (7, ""), (6, ""), (7, "")) * 2,
    "soul_251": (
        (2, "m7"),
        (5, "7"),
        (1, "maj7"),
        (1, "maj7"),
        (2, "m7"),
        (5, "7"),
        (1, "maj7"),
        (1, "maj7"),
    ),
    "major_145": ((1, ""), (1, ""), (4, ""), (1, ""), (5, ""), (4, ""), (1, ""), (5, "")),
}

_LETTERS = "CDEFGAB"
_NATURAL_CLASSES = (0, 2, 4, 5, 7, 9, 11)
_SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


def _degree_name(tonic: str, root: int, offset: int, degree: int) -> str:
    pitch = (root + offset) % 12
    letter_index = (_LETTERS.index(tonic[0]) + degree - 1) % 7
    difference = (pitch - _NATURAL_CLASSES[letter_index] + 1) % 12 - 1
    if difference in {-1, 0, 1}:
        return _LETTERS[letter_index] + {-1: "b", 0: "", 1: "#"}[difference]
    # The chord editor supports single accidentals.
    return (_FLAT_NAMES if tonic.endswith("b") else _SHARP_NAMES)[pitch]


def materialize_harmony(profile: MusicProfile) -> tuple[str, ...] | None:
    if profile.harmony == "keep":
        return None
    root = parse_chord(profile.tonic).root
    scale = _SCALES[profile.mode]
    return tuple(
        _degree_name(profile.tonic, root, scale[degree - 1], degree) + quality
        for degree, quality in _FORMULAS[profile.harmony]
    )
