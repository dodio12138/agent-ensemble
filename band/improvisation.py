"""Harmonic vocabulary and phrase choices for one eight-bar improvisation."""

import re
from dataclasses import dataclass
from itertools import permutations
from random import Random
from typing import Literal

from band.music import (
    TRACKS,
    Chord,
    FrozenModel,
    HarmonicTechnique,
    ImprovisationMove,
    ImprovisationPhrase,
    ImprovisationPlan,
    MusicProfile,
    MusicSettings,
    TrackId,
    TrioTrackId,
    parse_chord,
)

IMPROVISATION_PROMPT_VERSION = "improvisation-v2"
IMPROVISATION_PROMPT = """Conduct one eight-bar variation of the supplied trio theme.
Choose first-half move a and second-half move b by ID. Each option shows the real
preceding and following chords. For a smooth variation favor inversion or color.
A dominant leads into the following chord. Borrowed, deceptive and tritone moves
make a deliberate surprise that connects back. If the user asks for a surprise,
prefer one such move and let the other support it. Keep preserves written harmony.
Choose the order of three different lead instruments. For example, bass/piano/drums
means bass opens, piano answers, and drums lead the lift. Select an instrument for
the finish. Choose swell for a quiet opening with growing energy, trade for
call-and-response with gaps, or drive for an early build.
The fourth phrase always resolves into the opening harmony. Return JSON only."""

_ARCS = {
    "swell": ("space", "answer", "build", "resolve"),
    "trade": ("answer", "space", "answer", "resolve"),
    "drive": ("answer", "build", "build", "resolve"),
}
_COLORS = {
    "": ("add9", "6", "maj7"),
    "maj7": ("maj9", "6", "add9"),
    "maj9": ("maj7", "6", "add9"),
    "6": ("maj7", "add9", "maj9"),
    "add9": ("6", "maj7", "maj9"),
    "m": ("m7", "madd9", "m6"),
    "m7": ("m9", "m6", "madd9"),
    "m9": ("m7", "madd9", "m6"),
    "m6": ("m9", "m7", "madd9"),
    "madd9": ("m9", "m7", "m6"),
    "mMaj7": ("madd9", "m9"),
    "7": ("9", "13", "7b9"),
    "9": ("13", "7", "7b9"),
    "13": ("9", "7", "7b9"),
    "7b9": ("7", "9", "13"),
    "7#9": ("7", "9", "13"),
    "7b5": ("7", "9"),
    "7#5": ("7", "9"),
    "7sus4": ("sus4", "7", "9"),
    "sus2": ("sus4", "add9"),
    "sus4": ("7sus4", "sus2"),
    "dim": ("dim7", "m7b5"),
    "dim7": ("dim", "m7b5"),
    "m7b5": ("dim", "dim7"),
    "aug": ("7#5",),
}
_RIFF_COLORS = {
    "": ("add9", "sus2", "sus4"),
    "m": ("madd9", "m7"),
    "m7": ("m9", "madd9"),
    "7": ("7sus4", "9"),
    "sus2": ("add9", "sus4"),
    "sus4": ("sus2", "add9"),
}
_RIFF_GENRES = {"rock", "hard_rock", "alternative_rock", "pop", "reggae"}
_BASS_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_KEEP_HARMONY = re.compile(
    r"(?:不要|别|別|不用|不想|无需|無需)(?:改变|改變|更改|改|动|動|换|換)(?:和弦|和声|和聲)"
    r"|(?:和弦|和声|和聲)(?:完全|全部|都|保持)?(?:不变|不變|不改|照旧|照舊)"
    r"|(?:保留|保持)(?:原有|原来的|原來的|当前|當前|原)?(?:和弦|和声|和聲)"
    r"|\bkeep (?:the |my |current )?(?:chords|harmony)(?: unchanged)?\b",
    re.IGNORECASE,
)
_LEAD_NAMES = {
    "drums": r"鼓组?|\bdrums?\b",
    "bass": r"贝斯|貝斯|低音|\bbass\b",
    "piano": r"钢琴|鋼琴|键盘|鍵盤|\bpiano\b",
}
_LEAD = re.compile(
    "|".join(f"(?P<{track}>{pattern})" for track, pattern in _LEAD_NAMES.items()),
    re.IGNORECASE,
)
_ENTRANCES = (
    r"开头|開頭|开场|開場|先(?:来|來|弹|彈|领|領)|领奏|領奏|带头|帶頭|\bopens?\b|\bfirst\b|\bleads?\b",
    r"第二段|回应|回應|接话|接話|接力|\breply\b|\banswers?\b|\bresponds?\b",
    r"第三段|高点|高點|高潮|加码|加碼|\bpeak\b|\blift\b",
    r"第四段|收尾|结尾|結尾|落回|\bfinish\b|\bclose[sd]?\b",
)
_QUIET = re.compile(r"轻柔|輕柔|留白|慢慢|温柔|溫柔|低调|低調|quiet|soft|spacious", re.IGNORECASE)
_GROWTH = re.compile(r"先轻[再后]|先輕[再後]|由弱到强|由弱到強|\bbuild gradually\b", re.IGNORECASE)
_DRIVE = re.compile(
    r"有劲|有勁|加码|加碼|密一点|密一點|冲劲|衝勁|激烈|活力|drive|upbeat", re.IGNORECASE
)
_SURPRISE = re.compile(
    r"反[套俗]路|反常规|反常規|借用|欺骗|欺騙|三全音|出其不意|意外|"
    r"\b(?:surprise|unexpected|borrowed|deceptive|tritone)\b",
    re.IGNORECASE,
)
_REQUESTED_TECHNIQUE: dict[HarmonicTechnique, str] = {
    "inversion": r"转位|轉位|级进低音|級進低音|\b(?:inversions?|stepwise bass)\b",
    "dominant": r"副属|副屬|\bsecondary dominant\b",
    "borrowed": r"借用|\bborrowed\b",
    "deceptive": r"欺[骗騙]|\bdeceptive\b",
    "tritone": r"三全音|\btritone\b",
}
_ADVENTUROUS = {"borrowed", "deceptive", "tritone"}


@dataclass(frozen=True)
class HarmonicMove:
    position: int
    chord: str
    technique: HarmonicTechnique
    score: float

    def description(self, theme: tuple[str, ...]) -> str:
        return (
            f"bar {self.position + 1}: {theme[self.position - 1]} > {self.chord} > "
            f"{theme[(self.position + 1) % len(theme)]} ({self.technique})"
        )


class ImprovisationChoice(FrozenModel):
    a: str
    b: str
    order: str
    finish: TrioTrackId
    arc: Literal["swell", "trade", "drive"]


@dataclass(frozen=True)
class ImprovisationChoices:
    settings: MusicSettings
    first: dict[str, HarmonicMove | None]
    second: dict[str, HarmonicMove | None]
    orders: dict[str, tuple[TrackId, ...]]
    finishes: tuple[TrackId, ...]
    arcs: tuple[str, ...]
    requested_technique: HarmonicTechnique | None = None
    surprise: bool = False

    def summary(self) -> dict:
        direction = self.settings.conductor.profile
        return {
            "style": direction.genre if direction else self.settings.conductor.style,
            **({"groove": direction.groove, "mode": direction.mode} if direction else {}),
            "theme": self.settings.progression,
            "first_half": {
                key: move.description(self.settings.progression) if move else "keep written chords"
                for key, move in self.first.items()
            },
            "second_half": {
                key: move.description(self.settings.progression) if move else "keep written chords"
                for key, move in self.second.items()
            },
            "lead_orders": list(self.orders),
        }

    def schema(self) -> dict:
        options = (
            ("a", list(self.first)),
            ("b", list(self.second)),
            ("order", list(self.orders)),
            ("finish", list(self.finishes)),
            ("arc", list(self.arcs)),
        )
        return {
            "type": "object",
            "properties": {key: {"type": "string", "enum": values} for key, values in options},
            "required": [key for key, _ in options],
            "additionalProperties": False,
        }

    def materialize(self, choice: ImprovisationChoice) -> ImprovisationPlan:
        if choice.finish not in self.finishes or choice.arc not in self.arcs:
            raise ValueError("Model choice conflicts with the requested ending or arc")
        progression = list(self.settings.progression)
        moves = []
        for move in (self.first[choice.a], self.second[choice.b]):
            if move is not None:
                progression[move.position] = move.chord
                moves.append(
                    ImprovisationMove(
                        position=move.position, technique=move.technique, chord=move.chord
                    )
                )
        leads = (*self.orders[choice.order], choice.finish)
        return ImprovisationPlan(
            progression=tuple(progression),
            harmonic_moves=tuple(moves),
            phrases=tuple(
                ImprovisationPhrase(lead=lead, character=character)
                for lead, character in zip(leads, _ARCS[choice.arc], strict=True)
            ),
        )


def _riff_harmony(profile: MusicProfile | None) -> bool:
    return profile is not None and (
        profile.genre in _RIFF_GENRES or profile.harmony == "dorian_vamp"
    )


def _step(a: int, b: int) -> int:
    return min((a - b) % 12, (b - a) % 12)


def _tones(chord: Chord) -> set[int]:
    return {(chord.root + interval) % 12 for interval in chord.intervals}


def _connection(left: Chord, right: Chord) -> float:
    first, second = _tones(left), _tones(right)
    # Score the bass separately from the chord tones above it.
    motion = sum(min(_step(a, b) for b in second) for a in first) / len(first)
    motion += sum(min(_step(a, b) for a in first) for b in second) / len(second)
    bass = _step(left.bass, right.bass)
    return 0.8 * len(first & second) - motion - 0.3 * bass + (0.6 if bass == 5 else 0)


def _moves(
    settings: MusicSettings,
    positions: range,
    *,
    surprise: bool,
    requested: HarmonicTechnique | None,
) -> list[HarmonicMove]:
    chords = tuple(map(parse_chord, settings.progression))
    riff_harmony = _riff_harmony(settings.conductor.profile)
    colors = _RIFF_COLORS if riff_harmony else _COLORS
    moves = []
    for position in positions:
        chord = chords[position]
        previous, following = chords[position - 1], chords[(position + 1) % len(chords)]
        spelling, divider, slash = chord.symbol.partition("/")
        root_len = 2 if len(spelling) > 1 and spelling[1] in "b#" else 1
        root, quality = spelling[:root_len], spelling[root_len:]
        baseline = _connection(previous, chord) + _connection(chord, following)
        candidates: list[tuple[str, HarmonicTechnique, float]] = [
            (root + color + divider + slash, "color", 0.0) for color in colors.get(quality, ())
        ]
        inversion_tones = {(chord.root + interval) % 12 for interval in chord.intervals[:4]}
        for bass in sorted(inversion_tones - {chord.bass}):
            old_distance = _step(previous.bass, chord.bass) + _step(chord.bass, following.bass)
            new_distance = _step(previous.bass, bass) + _step(bass, following.bass)
            if (
                new_distance < old_distance
                and max(_step(previous.bass, bass), _step(bass, following.bass)) <= 4
            ):
                name = root if bass == chord.root else _BASS_NAMES[bass]
                candidates.append(
                    (spelling + ("/" + name if bass != chord.root else ""), "inversion", 1.0)
                )

        dominant = (following.root + 7) % 12
        above = (following.root + 1) % 12
        if (
            (not riff_harmony or surprise or requested == "dominant")
            and following.root != chord.root
            and dominant != chord.root
        ):
            candidates.append((_BASS_NAMES[dominant] + "7", "dominant", 1.35))
        if surprise:
            # Substitute only where the written chord already points at the target.
            if chord.root == dominant and chord.intervals[1] == 4:
                candidates.append((_BASS_NAMES[above] + "7", "tritone", 2.4))
            # IV -> iv -> I: the lowered third can fall into the tonic's fifth.
            if (
                chord.root == (following.root + 5) % 12
                and chord.intervals[1] == 4
                and following.intervals[1] == 4
            ):
                candidates.append((root + "m", "borrowed", 2.8))
            # V -> vi: replace an expected major tonic when the following bar restores it.
            if (
                chord.intervals[1] == 4
                and following.root == chord.root
                and following.intervals[1] == 4
                and previous.root == (chord.root + 7) % 12
                and previous.intervals[1] == 4
            ):
                candidates.append((_BASS_NAMES[(chord.root + 9) % 12] + "m", "deceptive", 2.2))
        for symbol, technique, function in candidates:
            alternate = parse_chord(symbol)
            score = _connection(previous, alternate) + _connection(alternate, following)
            score = (score - baseline) + function
            if score >= -1.75 or technique in _ADVENTUROUS:
                moves.append(HarmonicMove(position, alternate.symbol, technique, score))
    return moves


def _requested_entrances(instruction: str) -> dict[int, TrackId]:
    entrances = {}
    for clause in re.split(r"[。！？；，、;,.!?\n]", instruction):
        mentions = list(_LEAD.finditer(clause))
        for i, mention in enumerate(mentions):
            end = mentions[i + 1].start() if i + 1 < len(mentions) else len(clause)
            direction = clause[mention.end() : end][:20]
            # Prefer an explicitly named later phrase over a general "lead".
            slot = next(
                (
                    index
                    for index in (1, 2, 3, 0)
                    if re.search(_ENTRANCES[index], direction, re.IGNORECASE)
                ),
                None,
            )
            if slot is not None:
                entrances[slot] = mention.lastgroup
    return entrances


def improvisation_choices(
    instruction: str,
    settings: MusicSettings,
    variant: int,
) -> ImprovisationChoices:
    rng = Random(f"{settings.seed}:improv:{variant}")
    surprise = bool(_SURPRISE.search(instruction)) and not _QUIET.search(instruction)
    requested = next(
        (
            technique
            for technique, expression in _REQUESTED_TECHNIQUE.items()
            if re.search(expression, instruction, re.IGNORECASE)
        ),
        None,
    )
    quiet = bool(_QUIET.search(instruction))

    def choose(positions: range, *, allow_surprise: bool) -> dict[str, HarmonicMove | None]:
        options = _moves(settings, positions, surprise=allow_surprise, requested=requested)
        if not options or _KEEP_HARMONY.search(instruction):
            return {"keep": None}
        if allow_surprise:
            intentional = [move for move in options if move.technique in _ADVENTUROUS]
            exact = [move for move in intentional if move.technique == requested]
            options = exact or intentional or options

        def rank(move: HarmonicMove) -> float:
            emphasis = 4 if move.technique == requested else 0
            emphasis += 2 if allow_surprise and move.technique in _ADVENTUROUS else 0
            emphasis -= 1 if quiet and move.technique == "dominant" else 0
            return move.score + emphasis + rng.uniform(0, 0.35)

        ranked = sorted(options, key=rank, reverse=True)
        # Keep the list small without offering five variants of the same voicing.
        picked = []
        groups = set()
        for option in ranked:
            group = (option.position, option.technique)
            if group not in groups:
                groups.add(group)
                picked.append(option)
            if len(picked) == 5:
                break
        return {str(index + 1): option for index, option in enumerate(picked)}

    entrances = _requested_entrances(instruction)
    orders = list(permutations(TRACKS))
    for index in range(3):
        if (lead := entrances.get(index)) is not None:
            fitting = [order for order in orders if order[index] == lead]
            if fitting:
                orders = fitting
    rng.shuffle(orders)
    ending = (entrances[3],) if 3 in entrances else tuple(TRACKS)
    arcs = ("swell",) if _GROWTH.search(instruction) else tuple(_ARCS)
    early, late = range(1, 4), range(5, 8)
    requested_early = (
        surprise
        and requested in _ADVENTUROUS
        and any(
            move.technique == requested
            for move in _moves(settings, early, surprise=True, requested=requested)
        )
    )
    second = choose(late, allow_surprise=surprise and not requested_early)
    # Put at most one surprising move in the phrase, with stable bars between both edits.
    first = choose(
        early,
        allow_surprise=surprise
        and not any(move and move.technique in _ADVENTUROUS for move in second.values()),
    )
    return ImprovisationChoices(
        settings,
        first,
        second,
        {"/".join(order): order for order in orders},
        ending,
        arcs,
        requested,
        surprise,
    )


def rule_improvisation(
    instruction: str,
    choices: ImprovisationChoices,
    variant: int,
) -> ImprovisationPlan:
    rng = Random(f"{choices.settings.seed}:phrases:{variant}:{instruction}")
    quiet = _QUIET.search(instruction)
    arc_preference = (
        "swell"
        if quiet
        else "drive"
        if _DRIVE.search(instruction)
        else ("swell", "trade", "drive")[variant % 3]
    )
    arc = arc_preference if arc_preference in choices.arcs else choices.arcs[0]
    order = rng.choice(list(choices.orders))
    opening = choices.orders[order][0]

    def pick(options: dict[str, HarmonicMove | None]) -> str:
        moves = [(key, move) for key, move in options.items() if move]
        if not moves:
            return "keep"
        preferred = [key for key, move in moves if move.technique == choices.requested_technique]
        if choices.surprise and not preferred:
            preferred = [key for key, move in moves if move.technique in _ADVENTUROUS]
        if not preferred and arc == "drive" and not quiet:
            preferred = [
                key for key, move in moves if move.technique == "dominant" and move.score >= 0
            ]
        # Variation is among near-ranked choices, after checking both edges of each move.
        pool = preferred or [key for key, _ in moves[:3]]
        return pool[variant % len(pool)]

    return choices.materialize(
        ImprovisationChoice(
            a=pick(choices.first),
            b=pick(choices.second),
            order=order,
            finish=opening if opening in choices.finishes else choices.finishes[0],
            arc=arc,
        )
    )
