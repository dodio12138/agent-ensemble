"""Interpret natural-language conducting instructions outside the MIDI scheduler."""

import asyncio
import json
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal, get_args

import httpx2
from pydantic import Field

from band.harmony import materialize_harmony
from band.improvisation import (
    IMPROVISATION_PROMPT,
    IMPROVISATION_PROMPT_VERSION,
    ImprovisationChoice,
    improvisation_choices,
    rule_improvisation,
)
from band.music import (
    TIME_SIGNATURES,
    ConductorPlan,
    FrozenModel,
    Genre,
    ImprovisationPlan,
    MusicSettings,
    SongPlan,
    Style,
    TimeSignature,
)
from band.phrase_generation import (
    GRID_STEPS,
    PHRASE_PROMPT,
    chord_pitches,
    decode_phrase,
    phrase_grammar,
    song_scale,
)
from band.phrase_score import STEPS_PER_QUARTER, phrase_issues
from band.song import SONG_PROMPT, SongChoice, build_song, song_context
from band.style_direction import (
    STYLE_PROMPT,
    STYLE_PROMPT_VERSION,
    StyleChoice,
    StyleIntent,
    arrange_style,
    style_intent,
)
from band.timing_intent import (
    TIMING_PROMPT,
    TIMING_PROMPT_VERSION,
    TimingBoundary,
    TimingChoice,
    rule_timing,
    timing_intent,
)

PROMPT_VERSION = "conductor-v1"
RuleReason = Literal[
    "not_configured",
    "busy",
    "timeout",
    "unavailable",
    "invalid_response",
    "rejected",
    "unrecognized_reference",
    "score_required",
]

_SYSTEM_PROMPT = """You direct a trio of drums, bass and piano. Interpret the user's musical
instruction and return one complete JSON plan: style, intensity, drums, bass, piano.
Style is swing (摇摆/爵士), bossa (波萨诺瓦/巴萨诺瓦), funk (放克), or ballad (抒情).
Intensity is an integer from 0 to 100; a stated percentage is the exact new value.
For qualitative changes, adjust the current intensity by approximately 15.
Drums choose balanced, sparse, or driving. Bass chooses walking, root, or syncopated.
Piano chooses comp, sustained, or syncopated. When style changes, choose matching
instrument approaches. Otherwise retain untouched values from the current plan.
If the request is unrelated to conducting, return the current plan. Output JSON only."""

_STYLES = {
    "swing": r"摇摆|搖擺|爵士|\bswing\b|\bjazz\b",
    "bossa": r"[波巴][萨薩沙莎](?:诺瓦|諾瓦)?|\bbossa(?:[ -]nova)?\b",
    "funk": r"放克|\bfunk(?:y)?\b",
    "ballad": r"抒情|叙事曲|敘事曲|\bballad\b",
}
_STYLE_PRESETS = {
    "swing": {"drums": "balanced", "bass": "walking", "piano": "comp"},
    "bossa": {"drums": "sparse", "bass": "root", "piano": "syncopated"},
    "funk": {"drums": "driving", "bass": "syncopated", "piano": "syncopated"},
    "ballad": {"drums": "sparse", "bass": "root", "piano": "sustained"},
}
_PRESET_INTENSITIES = {"swing": 55, "bossa": 42, "funk": 76, "ballad": 30}
_STRENGTH = r"(?:强度|強度|力度|\bintensity\b|\benergy\b)"
_CHANGE = r"(?:提高|增加|调高|調高|降低|减少|減少|调低|調低|raise|increase|lower|reduce|decrease)"
_ABSOLUTE = re.compile(
    rf"{_STRENGTH}\s*(?:{_CHANGE}\s*)?(?:调整到|調整到|调到|調到|设为|設為|设置为|設定為|设成|改成|"
    r"改为|改為|到|为|為|是|to|at|=|:)?\s*(\d{1,3})(?!\d|\.)"
)
_RELATIVE = re.compile(
    rf"(?P<change>{_CHANGE})\s*(?:(?:the\s+)?{_STRENGTH}\s*)?"
    r"(?:by\s+)?(?P<amount>\d{1,3})\s*(?:个?百分点|個?百分點|点|點|points?\b|%)"
)
_DOWN = re.compile(
    rf"{_STRENGTH}\s*(?:{_CHANGE})?(?:弱|低|轻|輕|小|降)|"
    r"降低(?:一点|一些)?(?:强度|力度)?|减少强度|调低强度|弱一点|轻一点|輕一點|"
    r"柔和|轻柔|輕柔|轻松|輕鬆|放松|放鬆|收一点|收一點|\b(softer|quieter|calmer|less energetic)\b"
)
_UP = re.compile(
    rf"{_STRENGTH}\s*(?:更?强|更?強|更?高|提高|增加|调高)|"
    r"(?:提高|增加|调高)(?:一点|一些)?(?:强度|力度)|强一点|強一點|更强|更有劲|"
    r"用力|更激烈|热烈|熱烈|更有活力|\b(stronger|louder|harder|more energetic|more intense)\b"
)
_CLAUSE = re.compile(r"[,.!?;，。！？；\n]|而是|\binstead\b")
_NEGATION = re.compile(r"不要|不想要|不需要|别|別|避免|\b(no|not|without|don't|avoid)\b")
_ARRANGING_CLAUSES = re.compile(r"[,.!?;，。！？；\n]")
_EXACT_SCORE = re.compile(
    r"(?:按原谱|按原譜|照原谱|照原譜|原封不动|原封不動|一音不改)"
    r".{0,12}(?:演奏|播放|弹|彈|复现|復現)|"
    r"(?:演奏|播放|弹奏|彈奏|复现|復現).{0,12}(?:原曲|原谱|原譜|一音不改)|"
    r"\b(?:play|perform|reproduce)\b.{0,60}\b(?:note.for.note|verbatim|as written|exactly as recorded)\b"
    r"|\b(?:note.for.note|verbatim)\b.{0,30}\b(?:play|perform|reproduce)\b",
    re.IGNORECASE,
)
_APPROACHES = {
    "drums": (
        r"鼓组?|\bdrums?\b",
        {
            "sparse": r"稀一点|疏一点|少一点|简约|簡約|\bsparse\b",
            "driving": r"推进|推進|有冲劲|更有力|\bdriving\b",
            "balanced": r"均衡|\bbalanced\b",
        },
    ),
    "bass": (
        r"贝斯|貝斯|低音|\bbass\b",
        {
            "root": r"根音|\broots?\b",
            "walking": r"行进|行進|行走|\bwalking\b",
            "syncopated": r"切分|\bsyncopated?\b",
        },
    ),
    "piano": (
        r"钢琴|鋼琴|键盘|鍵盤|\bpiano\b",
        {
            "sustained": r"长(?:音|和弦)|長(?:音|和弦)|持续|持續|延长|\bsustained?\b",
            "syncopated": r"切分|短(?:音|和弦)|\bsyncopated?\b|\bstabs?\b",
            "comp": r"伴奏|\bcomp(?:ing)?\b",
        },
    ),
}


class ConductResult(FrozenModel):
    plan: ConductorPlan
    source: Literal["llm", "rules"]
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    attempts: int = 0
    reason: RuleReason | None = None
    progression: tuple[str, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    bpm: int | None = Field(default=None, ge=40, le=240, exclude_if=lambda value: value is None)
    meter: TimeSignature | None = Field(default=None, exclude_if=lambda value: value is None)
    timing_boundary: TimingBoundary | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ImprovisationResult(FrozenModel):
    plan: ImprovisationPlan
    source: Literal["llm", "rules"]
    model: str | None = None
    prompt_version: str = IMPROVISATION_PROMPT_VERSION
    attempts: int = 0
    reason: RuleReason | None = None


class CompositionResult(FrozenModel):
    song: SongPlan
    plan: ConductorPlan
    source: Literal["llm", "rules"]
    reason: RuleReason | None = None


@dataclass
class ModelAttempt[T]:
    result: T | None = None
    model: str | None = None
    attempts: int = 0
    reason: RuleReason | None = None


class ProviderFailure(Exception):
    def __init__(self, reason: RuleReason, *, retryable: bool = True):
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


def style_preset(style: Style) -> ConductorPlan:
    return ConductorPlan(
        style=style,
        intensity=_PRESET_INTENSITIES[style],
        **_STYLE_PRESETS[style],
    )


def genre_preset(
    genre: Genre, current: ConductorPlan
) -> tuple[ConductorPlan, tuple[str, ...] | None]:
    """Apply any supported genre at once with the local rules, without the model.

    The four core styles keep their hand-tuned presets. Other genres get the same
    groove, mode and typical harmony that a recognised text instruction would.
    """
    if genre in get_args(Style):
        return style_preset(genre), None  # type: ignore[arg-type]
    intent = StyleIntent(
        genre=genre,
        groove=None,
        mode=None,
        harmony=None,
        tonic=None,
        reference=None,
        curated=None,
        preserve_harmony=False,
        change_harmony=False,
        rich=True,
    )
    plan, harmony = arrange_style(intent, current, None)
    progression = materialize_harmony(plan.profile) if harmony != "keep" and plan.profile else None
    return plan, progression


def _allowed(text: str, start: int) -> bool:
    prefix = _CLAUSE.split(text[:start])[-1]
    return not _NEGATION.search(prefix)


def _is_performance_control(instruction: str) -> bool:
    text = unicodedata.normalize("NFKC", instruction).casefold().strip()
    if (
        re.fullmatch(r"\d{1,3}\s*%?", text)
        or _ABSOLUTE.search(text)
        or _RELATIVE.search(text)
        or _DOWN.fullmatch(text)
        or _UP.fullmatch(text)
        or re.search(_STRENGTH, text)
    ):
        return True
    for clause in _ARRANGING_CLAUSES.split(text):
        for name, options in _APPROACHES.values():
            if (match := re.search(name, clause)) and any(
                re.search(pattern, clause[match.end() :]) for pattern in options.values()
            ):
                return True
    return False


def _asks_for_exact_score(instruction: str) -> bool:
    text = unicodedata.normalize("NFKC", instruction)
    return any(_allowed(text.casefold(), match.start()) for match in _EXACT_SCORE.finditer(text))


def rule_plan(
    instruction: str,
    current: ConductorPlan,
    *,
    keep_style: bool = False,
) -> ConductorPlan:
    text = unicodedata.normalize("NFKC", instruction).casefold().strip()
    matches = [
        (match.start(), style)
        for style, pattern in _STYLES.items()
        for match in re.finditer(pattern, text)
        if _allowed(text, match.start())
    ]
    style = max(matches)[1] if matches and not keep_style else current.style
    intensity = current.intensity
    changes: list[tuple[int, int]] = []
    relative = next(
        (match for match in _RELATIVE.finditer(text) if _allowed(text, match.start())), None
    )
    if relative:
        direction = (
            -1
            if relative["change"]
            in {"降低", "减少", "減少", "调低", "調低", "lower", "reduce", "decrease"}
            else 1
        )
        changes.append((relative.start(), intensity + direction * int(relative["amount"])))
    for match in _ABSOLUTE.finditer(text):
        # In '强度提高 20 点', the number is relative even when the amount follows 强度.
        if re.match(r"\s*(?:个?百分点|個?百分點|点|點|points?\b)", text[match.end() :]):
            continue
        value = int(match[1])
        if value <= 100 and _allowed(text, match.start()):
            changes.append((match.start(), value))
    if (bare := re.fullmatch(r"(\d{1,3})\s*%?", text)) and int(bare[1]) <= 100:
        changes.append((0, int(bare[1])))
    for match in re.finditer(r"(?:^|[,.，。;；])\s*(\d{1,3})\s*%(?=\s*(?:$|[,.，。;；]))", text):
        if int(match[1]) <= 100:
            changes.append((match.start(), int(match[1])))
    if not changes:
        for pattern, offset in ((_DOWN, -15), (_UP, 15)):
            changes.extend(
                (match.start(), intensity + offset)
                for match in pattern.finditer(text)
                if _allowed(text, match.start())
            )
    if changes:
        intensity = min(100, max(0, max(changes, key=lambda item: item[0])[1]))
    approaches = dict(_STYLE_PRESETS[style]) if style != current.style else {}
    for clause in _ARRANGING_CLAUSES.split(text):
        for track, (name, options) in _APPROACHES.items():
            if instrument := re.search(name, clause):
                direction = clause[instrument.end() :]
                if _NEGATION.search(direction):
                    continue
                for approach, pattern in options.items():
                    if re.search(pattern, direction):
                        approaches[track] = approach
                        break
    return ConductorPlan.model_validate(
        {
            **current.model_dump(),
            **approaches,
            "style": style,
            "intensity": intensity,
            "profile": None if matches and not keep_style else current.profile,
        }
    )


def _decode[T: FrozenModel](
    body: bytes,
    configured_model: str,
    schema_model: type[T],
    expected_fields: set[str] | None = None,
) -> tuple[T, str]:
    try:
        completion = json.loads(body)
        choice = completion["choices"][0]
        message = choice["message"]
        content = message["content"]
        if (
            choice.get("finish_reason", "stop") != "stop"
            or message.get("refusal")
            or not isinstance(content, str)
            or len(content) > 2048
        ):
            raise ValueError
        data = json.loads(content)
        fields = schema_model.model_fields.keys() if expected_fields is None else expected_fields
        if not isinstance(data, dict) or data.keys() != fields:
            raise ValueError
        plan = schema_model.model_validate(data, strict=True)
        served_model = completion.get("model")
        model = (
            served_model[:160]
            if isinstance(served_model, str) and served_model
            else configured_model
        )
    except (IndexError, KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise ProviderFailure("invalid_response") from exc
    return plan, model


class Conductor:
    """Own the shared request budget for one application instance."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 24.0,
        phrase_generation: bool = False,
    ) -> None:
        if bool(base_url) != bool(model):
            raise ValueError("Configure both the conductor base URL and model")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Conductor timeout must be positive and finite")
        self.endpoint: str | None = None
        if base_url:
            uri = httpx2.URL(base_url)
            if (
                uri.scheme not in {"http", "https"}
                or not uri.host
                or uri.userinfo
                or uri.query
                or uri.fragment
            ):
                raise ValueError("Invalid conductor base URL")
            self.endpoint = str(uri).rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.phrase_generation = phrase_generation
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._active = False
        self._legacy_fields = set(ConductorPlan.model_fields) - {"profile"}
        schema = ConductorPlan.model_json_schema()
        self._schema = {
            "type": "object",
            "properties": {
                name: {
                    key: value for key, value in field.items() if key not in {"default", "title"}
                }
                for name, field in schema["properties"].items()
                if name in self._legacy_fields
            },
            "required": [name for name in schema["properties"] if name in self._legacy_fields],
            "additionalProperties": False,
        }

    async def _attempt[T](
        self,
        request: Callable[[httpx2.AsyncClient], Awaitable[tuple[T, str]]],
        *,
        budget: float | None = None,
    ) -> ModelAttempt[T]:
        if not self.endpoint or not self.model:
            return ModelAttempt(reason="not_configured")
        if self._active:
            return ModelAttempt(model=self.model, reason="busy")

        attempts = 0
        self._active = True
        reason: RuleReason = "unavailable"
        try:
            # The absolute deadline includes every attempt, response read, and backoff.
            async with asyncio.timeout(budget or self.timeout):
                async with httpx2.AsyncClient(
                    headers=self._headers,
                    timeout=httpx2.Timeout(self.timeout, connect=min(1.5, self.timeout)),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    for attempts in (1, 2):
                        try:
                            result, model = await request(client)
                            return ModelAttempt(result=result, model=model, attempts=attempts)
                        except ProviderFailure as exc:
                            reason = exc.reason
                            if not exc.retryable or attempts == 2:
                                break
                            await asyncio.sleep(0.18)
        except TimeoutError:
            reason = "timeout"
        finally:
            self._active = False
        return ModelAttempt(model=self.model, attempts=attempts, reason=reason)

    async def propose(self, instruction: str, current: ConductorPlan) -> ConductResult:
        if _asks_for_exact_score(instruction):
            return ConductResult(plan=current, source="rules", reason="score_required")
        intent = style_intent(instruction)
        performance_control = _is_performance_control(instruction)
        musical_traits = (
            intent.genre
            or intent.groove
            or intent.mode
            or intent.harmony
            or intent.tonic
            or intent.reference
            or intent.change_harmony
        )
        if intent.rich and (musical_traits or not performance_control):
            return await self._propose_style(instruction, current, intent)
        if not intent.genre and not performance_control:
            return await self._propose_style(
                instruction, current, replace(intent, descriptive=True)
            )
        attempt = await self._attempt(lambda client: self._request(client, instruction, current))
        plan = attempt.result or rule_plan(instruction, current)
        if attempt.result and intent.genre is None and current.profile is not None:
            plan = plan.model_copy(update={"style": current.style, "profile": current.profile})
        return ConductResult(
            plan=plan,
            source="llm" if attempt.result else "rules",
            model=attempt.model,
            attempts=attempt.attempts,
            reason=attempt.reason,
        )

    async def compose(self, instruction: str, current: MusicSettings) -> CompositionResult:
        async def request(client: httpx2.AsyncClient) -> tuple[SongChoice, str]:
            body = await self._complete(
                client,
                schema_name="composition",
                schema=SongChoice.model_json_schema(),
                prompt=SONG_PROMPT,
                context=song_context(current, instruction),
            )
            return _decode(body, self.model or "", SongChoice)

        attempt = await self._attempt(request)
        song, plan = build_song(current, instruction, attempt.result)
        if self.phrase_generation and attempt.result:
            song = await self._score_song(song, instruction, current, plan)
        return CompositionResult(
            song=song, plan=plan, source="llm" if attempt.result else "rules", reason=attempt.reason
        )

    async def _score_song(
        self, song: SongPlan, instruction: str, settings: MusicSettings, plan: ConductorPlan
    ) -> SongPlan:
        pitch_range = (67, 84) if settings.lineup == "modern" else (72, 86)
        scale = song_scale(song.tonic, song.mode)
        sections = song.sections
        scores = [[None] * section.bars for section in sections]

        async def request(client: httpx2.AsyncClient) -> tuple[bool, str]:
            prior: dict[str, list[int]] = {}
            themes: dict[tuple, list[list[int]]] = {}
            # Score the main themes first if the local inference budget runs out.
            order = sorted(range(len(sections)), key=lambda i: sections[i].role not in {"a", "b"})
            for index in order:
                section = sections[index]
                beats, denominator = TIME_SIGNATURES[section.meter]
                bar_steps = STEPS_PER_QUARTER * beats * 4 // denominator
                for bar in range(0, section.bars, 2):
                    pair = section.progression[bar], section.progression[bar + 1]
                    before = (
                        section.progression[bar - 1] if bar else sections[index - 1].progression[-1]
                    )
                    after = (
                        section.progression[bar + 2]
                        if bar + 2 < section.bars
                        else sections[(index + 1) % len(sections)].progression[0]
                    )
                    key = section.role, section.meter, bar, pair, after
                    notes = themes.get(key)
                    if notes is None:
                        context = {
                            "instruction": instruction,
                            "genre": plan.profile.genre if plan.profile else plan.style,
                            "instrument": "lead guitar"
                            if settings.lineup == "modern"
                            else section.keys_tone,
                            "section": section.role,
                            "pair_number": bar // 2 + 1,
                            "pairs_in_section": section.bars // 2,
                            "meter": section.meter,
                            "bpm": section.bpm,
                            "units_per_bar": bar_steps // GRID_STEPS,
                            "previous_chord": before,
                            "bar_1_chord": pair[0],
                            "bar_1_chord_notes": chord_pitches(pair[0], pitch_range),
                            "bar_2_chord": pair[1],
                            "cadence_choices": chord_pitches(pair[1], pitch_range),
                            "next_chord": after,
                            "next_chord_notes": chord_pitches(after, pitch_range),
                            "prior_phrase_pitches": prior.get(section.role) or prior.get("a", []),
                        }
                        try:
                            body = await self._chat(
                                client,
                                {
                                    "model": self.model,
                                    "temperature": 0.85,
                                    "max_tokens": 180,
                                    "grammar": phrase_grammar(bar_steps, pitch_range, scale, pair),
                                    "messages": [
                                        {"role": "system", "content": PHRASE_PROMPT},
                                        {
                                            "role": "user",
                                            "content": json.dumps(context, ensure_ascii=False),
                                        },
                                    ],
                                },
                            )
                            choice = json.loads(body)["choices"][0]
                            if choice.get("finish_reason") != "stop":
                                continue
                            draft = decode_phrase(choice["message"]["content"], bar_steps)
                            if phrase_issues(
                                draft, pair, after, bar_steps, pitch_range=pitch_range
                            ):
                                continue
                        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                            continue
                        except ProviderFailure:
                            return False, self.model or ""
                        notes = themes[key] = draft.notes
                    prior[section.role] = [row[2] for row in notes]
                    for offset in range(2):
                        scores[index][bar + offset] = tuple(
                            (start % bar_steps, duration, pitch, velocity)
                            for start, duration, pitch, velocity in notes
                            if start // bar_steps == offset
                        )
            return True, self.model or ""

        await self._attempt(request, budget=65.0)
        return song.model_copy(
            update={
                "sections": tuple(
                    section.model_copy(update={"lead": tuple(score)}) if any(score) else section
                    for section, score in zip(sections, scores, strict=True)
                )
            }
        )

    async def propose_with_timing(self, instruction: str, current: MusicSettings) -> ConductResult:
        if _asks_for_exact_score(instruction):
            return ConductResult(plan=current.conductor, source="rules", reason="score_required")
        intent = timing_intent(instruction, current)
        if not intent.recognized:
            return await self.propose(instruction, current.conductor)
        result = (
            await self.propose(intent.content, current.conductor)
            if intent.content
            else ConductResult(
                plan=current.conductor, source="rules", prompt_version=TIMING_PROMPT_VERSION
            )
        )
        bpm, meter = intent.bpm, intent.meter
        if intent.choose_bpm or intent.choose_meter:

            async def request(client: httpx2.AsyncClient) -> tuple[TimingChoice, str]:
                choose = [
                    key
                    for key, allowed in (("bpm", intent.choose_bpm), ("meter", intent.choose_meter))
                    if allowed
                ]
                body = await self._complete(
                    client,
                    schema_name="timing_direction",
                    schema=TimingChoice.model_json_schema(),
                    prompt=TIMING_PROMPT,
                    context={
                        "instruction": instruction,
                        "choose": choose,
                        "bpm": current.bpm,
                        "meter": current.meter,
                    },
                )
                return _decode(body, self.model or "", TimingChoice)

            attempt = await self._attempt(request)
            choice = attempt.result or rule_timing(instruction, intent, current)
            if intent.choose_bpm and 40 <= choice.bpm <= 240:
                bpm = choice.bpm
            if intent.choose_meter and choice.meter != "keep":
                meter = choice.meter
            if result.source == "rules":
                result = result.model_copy(
                    update={
                        "source": "llm" if attempt.result else "rules",
                        "model": attempt.model,
                        "attempts": attempt.attempts,
                        "reason": attempt.reason,
                    }
                )
        bpm = bpm if bpm != current.bpm else None
        meter = meter if meter != current.meter else None
        return result.model_copy(
            update={
                "bpm": bpm,
                "meter": meter,
                "timing_boundary": intent.boundary
                if bpm is not None or meter is not None
                else None,
            }
        )

    async def _propose_style(
        self,
        instruction: str,
        current: ConductorPlan,
        intent: StyleIntent,
    ) -> ConductResult:
        async def request(client: httpx2.AsyncClient) -> tuple[StyleChoice, str]:
            body = await self._complete(
                client,
                schema_name="style_direction",
                schema=StyleChoice.output_schema(),
                prompt=STYLE_PROMPT,
                context={
                    "instruction": instruction,
                    **intent.context(),
                    "current_harmony": current.profile.harmony if current.profile else None,
                },
            )
            return _decode(body, self.model or "", StyleChoice)

        attempt = await self._attempt(request)
        plan, harmony = arrange_style(intent, current, attempt.result)
        plan = rule_plan(instruction, plan, keep_style=True)
        progression = (
            materialize_harmony(plan.profile) if harmony != "keep" and plan.profile else None
        )
        return ConductResult(
            plan=plan,
            source="llm" if attempt.result else "rules",
            model=attempt.model,
            prompt_version=STYLE_PROMPT_VERSION,
            attempts=attempt.attempts,
            reason=(
                "unrecognized_reference"
                if intent.reference
                and not intent.curated
                and plan == current
                and harmony == "keep"
                and (
                    attempt.reason == "not_configured"
                    or (attempt.result and attempt.result.genre == "keep")
                )
                else attempt.reason
            ),
            progression=progression,
        )

    async def improvise(
        self,
        instruction: str,
        current: MusicSettings,
        variant: int,
    ) -> ImprovisationResult:
        choices = improvisation_choices(instruction, current, variant)

        async def request(client: httpx2.AsyncClient) -> tuple[ImprovisationPlan, str]:
            body = await self._complete(
                client,
                schema_name="improvisation",
                schema=choices.schema(),
                prompt=IMPROVISATION_PROMPT,
                context={**choices.summary(), "instruction": instruction},
            )
            choice, model = _decode(body, self.model or "", ImprovisationChoice)
            try:
                return choices.materialize(choice), model
            except (KeyError, ValueError) as exc:
                raise ProviderFailure("invalid_response") from exc

        attempt = await self._attempt(request)
        return ImprovisationResult(
            plan=attempt.result or rule_improvisation(instruction, choices, variant),
            source="llm" if attempt.result else "rules",
            model=attempt.model,
            attempts=attempt.attempts,
            reason=attempt.reason,
        )

    async def _request(
        self,
        client: httpx2.AsyncClient,
        instruction: str,
        current: ConductorPlan,
    ) -> tuple[ConductorPlan, str]:
        body = await self._complete(
            client,
            schema_name="conductor_plan",
            schema=self._schema,
            prompt=_SYSTEM_PROMPT,
            context={
                "current": current.model_dump(exclude={"profile"}),
                "instruction": instruction,
            },
        )
        return _decode(body, self.model or "", ConductorPlan, self._legacy_fields)

    async def _complete(
        self,
        client: httpx2.AsyncClient,
        *,
        schema_name: str,
        schema: dict,
        prompt: str,
        context: dict,
    ) -> bytes:
        assert self.endpoint and self.model
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": 0,
            "max_tokens": 192,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        }
        return await self._chat(client, payload)

    async def _chat(self, client: httpx2.AsyncClient, payload: dict) -> bytes:
        assert self.endpoint
        body = bytearray()
        try:
            async with client.stream("POST", self.endpoint, json=payload) as response:
                status = response.status_code
                if status == 408:
                    raise ProviderFailure("timeout")
                if status == 429 or status >= 500:
                    raise ProviderFailure("unavailable")
                if status != 200:
                    raise ProviderFailure("rejected", retryable=False)
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 32_768:
                        raise ProviderFailure("invalid_response", retryable=False)
                    body.extend(chunk)
        except httpx2.TimeoutException as exc:
            raise ProviderFailure("timeout") from exc
        except httpx2.RequestError as exc:
            raise ProviderFailure("unavailable") from exc
        return bytes(body)
