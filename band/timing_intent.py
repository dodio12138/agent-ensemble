"""Recognize a requested tempo or meter change without rewriting the arrangement."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from band.music import FrozenModel, MusicSettings, TimeSignature

TimingBoundary = Literal["next_chorus"]
TIMING_PROMPT_VERSION = "timing-direction-v1"
TIMING_PROMPT = """Choose the smallest useful change for a single new section in
this live band's performance. Only change the fields listed in 'choose'. Use
'keep' and 0 for unchanged meter and BPM. BPM counts quarter notes, even in 6/8.
4/4 has four quarter-note beats, 3/4 is a three-beat waltz, and 6/8 groups six
eighth notes into two pulses of three. Use 3/4 or 6/8 only when the instruction
suggests that grouping, not for generic intensity or swing. To make room, a
modest tempo reduction of roughly 8–12 BPM usually suffices. If there is no
clear musical reason to change, keep it. Return JSON only."""

_SPLIT = re.compile(r"[,，;；。！？!?\n]|(?<!\d)\.|\.(?!\d)")
_NEGATIVE = re.compile(
    r"不要|别|別|不用|不必|无需|無需|不许|不許|避免|\b(?:do not|don't|never|avoid|without)\b"
)
_REPLACE = re.compile(r"而是|但是|不过|不過|\binstead\b|\bbut\b")
_AXIS_BPM = r"(?:bpm(?![a-z])|速度|曲速|\btempo\b)"
_AXIS_METER = r"(?:拍号|拍號|拍子|节拍|節拍|\b(?:time signature|meter|metre)\b)"
_HOLD = r"(?:保留|保持|维持|維持|不(?:要|用|必|需)?(?:改变|改變|改|变|變|动|動|换|換|调整|調整)|keep|preserve|leave|unchanged)"
_SAME = r"(?:保持不变|保持不變|照旧|照舊|不动|不動|不变|不變|不改|不换|不換|unchanged|(?:stays? )?the same)"
_HOLD_BOTH = re.compile(
    rf"(?:{_HOLD}\s*{_AXIS_BPM}\s*(?:和|及|与|與|and)\s*{_AXIS_METER}|{_HOLD}\s*{_AXIS_METER}\s*(?:和|及|与|與|and)\s*{_AXIS_BPM})"
)
_NUMBER = r"(?<![\d./+\-])(?P<bpm>\d+)(?![\d./])"
_TEMPO = (
    re.compile(rf"{_NUMBER}\s*bpm(?![a-z])"),
    re.compile(
        rf"{_AXIS_BPM}\s*(?:(?:设(?:为|為|置)|调(?:到|为|為)|调整(?:为|為|到)|"
        rf"改(?:到|为|為|成)|变(?:成|为|為)|維持在|维持在|到|为|為|是|变到|set|to|at|=|:)\s*)*{_NUMBER}"
        rf"(?!\s*(?:短语|短語|拍号|拍號|%|[点點]|小节|小節|[+-]))"
    ),
    re.compile(
        rf"{_AXIS_BPM}\s*(?:从|從|from)\s*\d+\s*(?:bpm\s*)?"
        rf"(?:改(?:到|成|为|為)|调(?:到|为|為)|上到|降到|变到|變到|到|至|to)\s*{_NUMBER}"
    ),
)
_DECREASE = r"减慢|減慢|放慢|慢(?:一)?点|慢(?:一)?點|降速|减速|減速|降低|降|减|減|\b(?:slow(?:er)?(?: down)?|lower|decrease|reduce)\b"
_INCREASE = r"加快|加速|快(?:一)?点|快(?:一)?點|提速|提高|上调|上調|加|\b(?:faster|speed up|raise|increase)\b"
_DIRECTION = rf"(?:{_DECREASE}|{_INCREASE})"
_RELATIVE = re.compile(
    rf"(?:(?P<sign>[+-])\s*(?P<signed>\d{{1,3}})\s*(?P<signed_unit>bpm(?![a-z])|(?=$))"
    rf"|(?P<direction>{_DIRECTION})\s*(?:{_AXIS_BPM}\s*)?(?:by\s*)?"
    rf"(?P<amount>\d{{1,3}})(?![\d./])\s*(?P<unit>bpm(?![a-z])|%|个?[点點]|beats?\b|(?=$)))"
)
_METER_NUMBER = re.compile(r"(?<!\d)(?P<top>\d+)\s*/\s*(?P<bottom>\d+)(?!\d)(?:\s*拍)?")
_METER_NAMES: dict[TimeSignature, re.Pattern[str]] = {
    "3/4": re.compile(
        r"三四拍|四分之三拍?|(?<![\d/])[三3]\s*拍子|每小[节節][三3]个四分|"
        r"華爾茲|华尔兹|圆舞曲|圓舞曲|\b(?:waltz|three quarter.note beats)\b"
    ),
    "6/8": re.compile(
        r"六八拍|八分之六拍?|[两兩二2]\s*(?:个|個)?\s*大拍[,，\s]*(?:每拍|各拍|各自)?"
        r"(?:分(?:成|为|為))?[三3]\s*(?:个|個)?\s*(?:八分|等份)|"
        r"\b(?:compound duple|two pulses of three|two dotted.quarter (?:beats|pulses))\b"
    ),
    "4/4": re.compile(r"四四拍|四分之四拍?|\bcommon time\b"),
}
_METERS = frozenset(_METER_NAMES)
_DELEGATE = re.compile(
    r"按需|自行|酌情|根据(?:乐句|实际|樂句|實際|音乐|音樂|编排|編排)|有需要|"
    r"需要时|需要時|看(?:情況|情况|需要)|(?:你|指挥|指揮)(?:来|來)?(?:决定|決定|选|選|定)|"
    r"要不要|是否(?:需要|改|变|變|调整|調整)|\b(?:if (?:needed|appropriate|necessary)|"
    r"(?:you|conductor) decide|at your discretion|as (?:needed|appropriate))\b"
)
_NEXT_CHORUS = re.compile(
    r"下一(?:轮|輪|段|个八小节|個八小節)|下一个(?:乐句周期|樂句週期)|\bnext (?:chorus|section|eight bars|8 bars)\b"
)
_MULTIPLE = re.compile(
    r"每(?:一)?(?:段(?:落)?|轮|輪|小节|小節)(?:都|分别|分別|交替)|"
    r"(?:前|后|後)\s*(?:[一二两兩三四五六七八九十\d]+)\s*(?:小节|小節)|"
    r"(?:进入|進入|到|等到|等|进|進)(?:副歌|主歌|桥段|橋段)|"
    r"\b(?:every (?:section|bar|chorus)|alternate (?:between|meters?|tempos?)|"
    r"first [234] bars|when (?:the|we reach the) (?:verse|chorus|bridge))\b"
)
_FORM_STAGE = re.compile(r"主歌|副歌|桥段|橋段|前奏|尾奏|\b(?:verse|intro|bridge|outro)\b")
_ENDING = re.compile(r"结尾|結尾|收束|落(?:点|點)|\b(?:ending|resolve|cadence)\b")
_ENERGY = re.compile(r"推进|推進|推力|更紧|更緊|紧迫|緊迫|冲刺|衝刺|\b(?:push|urgent|driving)\b")
_SPACE = re.compile(
    r"留白|呼吸|从容|從容|放松|放鬆|喘口气|喘口氣|舒展|\b(?:space|relaxed|more room)\b"
)
_NON_TIMING = re.compile(r"力度|强度|強度|密度|音量|\b(?:intensity|volume|density)\b")
_DIRECTION_INSTRUMENT = re.compile(
    r"鼓(?:手|组|組)?|贝斯|貝斯|钢琴|鋼琴|吉他|键盘|鍵盤|\b(?:drums?|bass|piano|guitars?|keys)\b"
)
_FEEL = re.compile(r"半速(?:律动|律動|拍感|节奏|節奏)|double.time|half.time|三连音|三連音")
_MODEST_TEMPO = re.compile(
    r"(?:让音乐|讓音樂|整体|整體|全队|全隊|please\s*)?(?:稍微|略微|再)?\s*"
    r"(?:慢一点|快一点|慢一點|快一點|(?:放慢|减速|減速|加快|提速|加速|降速)(?:一些|一点|一點)?"
    r"|slower|faster|slow down|speed up)\s*"
)
_GROUP_SEPARATOR = re.compile(r"([两兩二2]\s*(?:个|個)?\s*大拍)\s*[,，]\s*(每拍|各拍|各自)")


def _tempo_purpose(text: str) -> bool:
    return bool(
        _SPACE.search(text)
        or _ENERGY.search(text)
        or _ENDING.search(text)
        or re.search(_DIRECTION, text)
    )


class TimingChoice(FrozenModel):
    bpm: int = Field(ge=0, le=240, strict=True)
    meter: Literal[TimeSignature, "keep"]


@dataclass(frozen=True, slots=True)
class TimingIntent:
    bpm: int | None = None
    meter: TimeSignature | None = None
    choose_bpm: bool = False
    choose_meter: bool = False
    boundary: TimingBoundary | None = None
    content: str = ""
    timing_only: bool = False

    @property
    def recognized(self) -> bool:
        return self.timing_only or self.choose_bpm or self.choose_meter


def _hold(text: str, axis: str) -> bool:
    return bool(
        re.search(
            rf"(?:{_HOLD}\s*(?:当前|原有|原来|原來|原|the|current)*\s*{axis}|{axis}\s*{_SAME})",
            text,
        )
    )


def _allowed(text: str, start: int) -> bool:
    part = _REPLACE.split(text[:start])[-1]
    # "要不要" asks for a judgment instead of prohibiting a change.
    return _NEGATIVE.search(part.replace("要不要", "")) is None


def _absolute(text: str) -> tuple[bool, int | None]:
    candidates = [
        (match.start("bpm"), int(match["bpm"]))
        for pattern in _TEMPO
        for match in pattern.finditer(text)
        if _allowed(text, match.start())
        and not re.search(rf"(?:{_DIRECTION})\s*(?:by\s*)?[+-]?\s*$", text[: match.start("bpm")])
        and not re.search(r"-\s*$", text[: match.start("bpm")])
        and not re.match(r"\s*(?:[–—-]|至|到|\bto\b)\s*\d", text[match.end() :])
    ]
    if not candidates:
        return False, None
    value = max(candidates)[1]
    return True, value if 40 <= value <= 240 else None


def _meter(text: str) -> tuple[bool, TimeSignature | None]:
    candidates = [
        (match.start(), f"{match['top']}/{match['bottom']}")
        for match in _METER_NUMBER.finditer(text)
        if _allowed(text, match.start())
    ]
    candidates.extend(
        (match.start(), meter)
        for meter, pattern in _METER_NAMES.items()
        for match in pattern.finditer(text)
        if _allowed(text, match.start())
    )
    if not candidates:
        return False, None
    value = max(candidates)[1]
    return True, value if value in _METERS else None


def _relative_tempo(text: str, baseline: int) -> tuple[bool, int | None]:
    explicit = bool(re.search(_AXIS_BPM, text))
    for match in _RELATIVE.finditer(text):
        if not _allowed(text, match.start()):
            continue
        if not explicit and not (
            match["signed_unit"] or match["unit"] and match["unit"].startswith("bpm")
        ):
            continue
        if match["sign"]:
            difference = int(match["sign"] + match["signed"])
        else:
            amount = int(match["amount"])
            if match["unit"] == "%":
                amount = round(baseline * amount / 100)
            difference = -amount if re.fullmatch(_DECREASE, match["direction"]) else amount
        return True, min(240, max(40, baseline + difference))
    modest = _MODEST_TEMPO.fullmatch(_NEXT_CHORUS.sub("", text).strip())
    if not (explicit or modest) or (_FEEL.search(text) and not explicit):
        return False, None
    for pattern, difference in ((_DECREASE, -10), (_INCREASE, 10)):
        if (match := re.search(pattern, text)) and _allowed(text, match.start()):
            return True, min(240, max(40, baseline + difference))
    return False, None


def timing_intent(instruction: str, current: MusicSettings) -> TimingIntent:
    """A single change is supported, optionally queued for the next chorus."""
    text = unicodedata.normalize("NFKC", instruction).casefold()
    boundary = "next_chorus" if _NEXT_CHORUS.search(text) else None
    bpm = meter = None
    choose_bpm = choose_meter = False
    recognized = False
    blocked = False
    # Count phrases separated by punctuation together when the wording specifies
    # how each of the two large beats is subdivided.
    text = _GROUP_SEPARATOR.sub(r"\1\2", text)
    for raw in _SPLIT.split(text):
        clause = raw.strip()
        if not clause:
            continue
        blocked |= any(
            _allowed(clause, match.start())
            for pattern in (_MULTIPLE, _FORM_STAGE)
            for match in pattern.finditer(clause)
        )
        hold_both = bool(_HOLD_BOTH.search(clause))
        hold_bpm = hold_both or _hold(clause, _AXIS_BPM)
        hold_meter = hold_both or _hold(clause, _AXIS_METER)
        absolute, exact_bpm = _absolute(clause)
        explicit_meter, exact_meter = _meter(clause)
        discretionary = any(_allowed(clause, match.start()) for match in _DELEGATE.finditer(clause))
        asks_bpm = bool(re.search(_AXIS_BPM, clause))
        asks_meter = bool(re.search(_AXIS_METER, clause))
        relative, delta_bpm = (
            _relative_tempo(clause, current.bpm) if not absolute else (False, None)
        )
        if discretionary and asks_bpm:
            relative, delta_bpm = False, None
        recognized |= bool(
            absolute
            or explicit_meter
            or _METER_NUMBER.search(clause)
            or any(pattern.search(clause) for pattern in _METER_NAMES.values())
            or hold_bpm
            or hold_meter
            or asks_bpm
            or asks_meter
            or relative
        )
        if absolute or relative or hold_bpm:
            bpm = None if hold_bpm else exact_bpm if absolute else delta_bpm
            choose_bpm = False
        elif discretionary and asks_bpm:
            choose_bpm = True
        if explicit_meter or hold_meter:
            meter = None if hold_meter else exact_meter
            choose_meter = False
        elif discretionary and asks_meter:
            choose_meter = True
    if blocked:
        bpm = meter = None
        choose_bpm = choose_meter = False
    # Generic permission to adjust timing is not musical direction on its own.
    choose_bpm = choose_bpm and _tempo_purpose(text)
    # Meter is a structural change. Require evidence of a different grouping
    # even when the user explicitly delegates that choice.
    choose_meter = choose_meter and (
        meter is not None
        or re.search(r"三拍|[两兩]大拍|三分组|三分組|\b(?:triple|compound)\b", text) is not None
    )
    # Strip timing-only clauses before the style model. Avoid treating a tempo
    # reduction as a lower intensity, or "Current BPM" as an artist reference.
    content = _style_content(instruction, choose_bpm or choose_meter) if recognized else instruction
    return TimingIntent(bpm, meter, choose_bpm, choose_meter, boundary, content, recognized)


def _style_content(instruction: str, delegated: bool) -> str:
    """Keep playing instructions and leave timing-only clauses to the timing interpreter."""
    from band.style_direction import style_intent

    clauses = []
    normalized = _GROUP_SEPARATOR.sub(r"\1\2", unicodedata.normalize("NFKC", instruction))
    for clause in _SPLIT.split(normalized):
        text = clause.casefold()
        style = style_intent(clause)
        scoped_purpose = delegated and (
            _SPACE.search(text) or _ENDING.search(text) or _ENERGY.search(text)
        )
        timed = (
            re.search(_AXIS_BPM, text)
            or re.search(_AXIS_METER, text)
            or _METER_NUMBER.search(text)
            or any(pattern.search(text) for pattern in _METER_NAMES.values())
            or _relative_tempo(text, 120)[0]
        )
        keep = (
            (
                not timed
                and not scoped_purpose
                and _NEXT_CHORUS.fullmatch(text.strip()) is None
                and not (_MULTIPLE.search(text) and _NEGATIVE.search(text))
            )
            or style.genre
            or style.groove
            or style.harmony
            or style.tonic
            or _NON_TIMING.search(text)
            or re.search(
                r"风格|風格|参考|參考|(?<!拍)像|\b(?:style|energy|like)\b",
                text,
            )
            or (_DIRECTION_INSTRUMENT.search(text) and not _DELEGATE.search(text))
        )
        if keep:
            if timed:
                clause = _METER_NUMBER.sub("", clause)
                for pattern in (*_TEMPO, *_METER_NAMES.values()):
                    clause = pattern.sub("", clause)
            clauses.append(clause)
    return "，".join(clause.strip() for clause in clauses if clause.strip())


def rule_timing(instruction: str, intent: TimingIntent, current: MusicSettings) -> TimingChoice:
    text = instruction.casefold()
    bpm = 0
    if intent.choose_bpm:
        if _SPACE.search(text) or _ENDING.search(text) or re.search(_DECREASE, text):
            bpm = max(40, current.bpm - 10)
        elif _ENERGY.search(text) or re.search(_INCREASE, text):
            bpm = min(240, current.bpm + 10)
    return TimingChoice(bpm=bpm, meter="keep")
