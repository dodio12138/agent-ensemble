"""Bounded musical proposals among the three rhythm-section players."""

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from band.music import (
    PPQ,
    MusicSettings,
    NegotiationEvent,
    Note,
    TrackId,
    validate_notes,
)

Kind = Literal["kick_pattern", "reserve_space", "lighter_drums"]
CUES: tuple[tuple[TrackId, TrackId, Kind], ...] = (
    ("drums", "bass", "kick_pattern"),
    ("bass", "piano", "reserve_space"),
    ("piano", "drums", "lighter_drums"),
)
MAX_PENDING_REPLIES = 4
KICK = 36
CYMBALS = {42, 44, 46, 49, 51, 55, 57, 59}
FILL_PITCHES = {38, 40, 41, 43, 45, 47, 48, 50}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Offer:
    ticks: tuple[int, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class Reply:
    detail: str
    notes: tuple[Note, ...] | None = None


class Participant(Protocol):
    def propose(self, notes: tuple[Note, ...], settings: MusicSettings) -> Offer | None: ...

    async def respond(
        self,
        offer: NegotiationEvent,
        notes: tuple[Note, ...],
        settings: MusicSettings,
    ) -> Reply: ...


def _near(tick: int, anchors: tuple[int, ...]) -> bool:
    return any(abs(tick - anchor) <= PPQ // 2 for anchor in anchors)


def _ornament(note: Note, anchors: tuple[int, ...], pulse_ticks: int) -> bool:
    return (
        note.start_tick % pulse_ticks != 0
        and note.pitch in CYMBALS | FILL_PITCHES
        and _near(note.start_tick, anchors)
    )


class DrumNegotiator:
    def propose(self, notes: tuple[Note, ...], settings: MusicSettings) -> Offer | None:
        anchors = tuple(sorted({note.start_tick for note in notes if note.pitch == KICK}))
        if anchors:
            return Offer(anchors[:6], "这些底鼓是本小节的重音，贝斯能锁住其中的起音吗？")
        return None

    async def respond(
        self,
        offer: NegotiationEvent,
        notes: tuple[Note, ...],
        settings: MusicSettings,
    ) -> Reply:
        if settings.conductor.drums == "driving":
            return Reply("这段需要保持推进感，保留原来的鼓组织体。")
        kept = tuple(
            note for note in notes if not _ornament(note, offer.ticks, settings.pulse_ticks)
        )
        if kept == notes:
            return Reply("和弦附近已经没有额外的镲片或加花，保留当前节奏。")
        return Reply("撤掉附近的加花，保留底鼓和正拍，让钢琴的和弦更清楚。", kept)


class BassNegotiator:
    def propose(self, notes: tuple[Note, ...], settings: MusicSettings) -> Offer | None:
        anchors = tuple(
            note.start_tick for note in notes if note.start_tick >= settings.pulse_ticks
        )
        if anchors:
            return Offer(anchors[-2:], "后半小节的低音想更清楚，钢琴能在这些起音附近留一点空吗？")
        return None

    async def respond(
        self,
        offer: NegotiationEvent,
        notes: tuple[Note, ...],
        settings: MusicSettings,
    ) -> Reply:
        # Tighten existing matches and, if there is room, align one nearby offbeat.
        chosen = []
        moved = False
        aligned = 0
        for index, note in enumerate(notes):
            tick = note.start_tick
            if not moved and index and tick not in offer.ticks and tick % settings.pulse_ticks:
                preceding = chosen[-1]
                last_end = preceding.start_tick + preceding.duration_ticks
                next_start = (
                    notes[index + 1].start_tick if index + 1 < len(notes) else settings.bar_ticks
                )
                possible = (
                    anchor
                    for anchor in offer.ticks
                    if last_end <= anchor < next_start and abs(anchor - tick) <= PPQ // 6
                )
                nearest = min(possible, key=lambda anchor: abs(anchor - tick), default=tick)
                moved = nearest != tick
                tick = nearest
            if tick in offer.ticks:
                aligned += 1
                next_start = (
                    notes[index + 1].start_tick if index + 1 < len(notes) else settings.bar_ticks
                )
                duration = min(note.duration_ticks * 3 // 4 or 1, next_start - tick)
                note = note.model_copy(update={"start_tick": tick, "duration_ticks": duration})
            chosen.append(note)
        if not aligned:
            return Reply("当前低音句法没有能靠拢的起点，保留原节奏。")
        return Reply(
            f"和 {aligned} 个底鼓起音对齐，缩短收尾，给下一拍留出空间。",
            tuple(chosen),
        )


class PianoNegotiator:
    def propose(self, notes: tuple[Note, ...], settings: MusicSettings) -> Offer | None:
        onsets = sorted({note.start_tick for note in notes})
        if onsets:
            anchors = (onsets[-1],)
            # For a held chord, ask around the middle of its sustain instead of its attack.
            if len(onsets) == 1 and max(note.duration_ticks for note in notes) >= 2 * PPQ:
                anchors = (onsets[0] + PPQ // 2,)
            return Offer(anchors, "这句和弦想留出空间，鼓组能省掉附近的装饰音吗？")
        return None

    async def respond(
        self,
        offer: NegotiationEvent,
        notes: tuple[Note, ...],
        settings: MusicSettings,
    ) -> Reply:
        onsets = sorted({note.start_tick for note in notes})
        melody = {note.start_tick for note in notes if note.event_id.endswith("-motif")}
        conflicts = [
            tick for tick in onsets if tick != 0 and tick not in melody and _near(tick, offer.ticks)
        ]
        if not conflicts:
            return Reply("钢琴已经避开了低音起点，沿用原伴奏。")
        if len(onsets) < 2:
            return Reply("这是本小节唯一的和声落点，保留该和弦。")
        omitted = conflicts[-1]
        return Reply(
            "少打一组后半拍和弦，给低音起音留出空位。",
            tuple(note for note in notes if note.start_tick != omitted),
        )


DEFAULT_PARTICIPANTS: Mapping[TrackId, Participant] = {
    "drums": DrumNegotiator(),
    "bass": BassNegotiator(),
    "piano": PianoNegotiator(),
}


class Negotiator:
    """Coordinates messages on the planning loop while playback remains independent."""

    def __init__(
        self,
        participants: Mapping[TrackId, Participant] | None,
        deadline: float,
    ) -> None:
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("Negotiation deadline must be positive and finite")
        if participants and (unknown := participants.keys() - DEFAULT_PARTICIPANTS.keys()):
            raise ValueError(f"Unknown negotiating musicians: {sorted(unknown)}")
        self._reply_timeout = deadline / len(CUES)
        self._participants = {**DEFAULT_PARTICIPANTS, **(participants or {})}
        self._pending: dict[TrackId, set[asyncio.Task[Reply]]] = {
            track: set() for track in self._participants
        }

    async def run(
        self,
        bar: int,
        version: int,
        settings: MusicSettings,
        notes: dict[TrackId, tuple[Note, ...]],
        ready: set[TrackId],
        notify: Callable[[NegotiationEvent], None] | None,
    ) -> tuple[NegotiationEvent, ...]:
        messages: list[NegotiationEvent] = []

        def publish(message: NegotiationEvent) -> None:
            messages.append(message)
            if notify:
                notify(message)

        for sender, recipient, kind in CUES:
            if sender not in ready or recipient not in ready:
                continue
            try:
                offer = self._participants[sender].propose(notes[sender], settings)
                if offer is None:
                    continue
                if any(tick >= settings.bar_ticks for tick in offer.ticks):
                    raise ValueError("Offer exceeds the current measure")
                proposal = NegotiationEvent(
                    proposal_id=f"{version}:{bar}:{sender}:{recipient}",
                    stage="proposal",
                    sender=sender,
                    recipient=recipient,
                    kind=kind,
                    ticks=offer.ticks,
                    detail=offer.detail,
                )
            except Exception as error:  # noqa: BLE001 - an invalid offer cannot discard the draft
                logger.warning("Skipping invalid %s proposal: %s", sender, type(error).__name__)
                continue
            publish(proposal)
            try:
                reply = await self._ask(proposal, notes[recipient], settings)
                amended = _adjudicate(bar, proposal, notes[recipient], reply, settings)
            except TimeoutError:
                publish(
                    proposal.model_copy(
                        update={
                            "stage": "timeout",
                            "detail": "未在截止前收到回应，继续使用原编排。",
                        }
                    )
                )
            except Exception:  # noqa: BLE001 - a peer failure cannot discard the bar draft
                publish(
                    proposal.model_copy(
                        update={
                            "stage": "declined",
                            "detail": "回应无效，继续使用原编排。",
                        }
                    )
                )
            else:
                stage = "accepted" if amended is not None else "declined"
                if amended is not None:
                    notes[recipient] = amended
                publish(proposal.model_copy(update={"stage": stage, "detail": reply.detail}))
        return tuple(messages)

    async def _ask(
        self,
        proposal: NegotiationEvent,
        notes: tuple[Note, ...],
        settings: MusicSettings,
    ) -> Reply:
        pending = self._pending[proposal.recipient]
        if len(pending) >= MAX_PENDING_REPLIES:
            raise TimeoutError("Peer reply queue is full")
        task = asyncio.create_task(
            self._participants[proposal.recipient].respond(proposal, notes, settings),
        )
        pending.add(task)

        def retire(finished: asyncio.Task[Reply]) -> None:
            pending.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(retire)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._reply_timeout)
        finally:
            if not task.done():
                task.cancel()
        if not done:
            raise TimeoutError("Peer reply deadline passed")
        if task.cancelled():
            raise ValueError("Peer cancelled its response")
        return task.result()


def _adjudicate(
    bar: int,
    proposal: NegotiationEvent,
    original: tuple[Note, ...],
    reply: Reply,
    settings: MusicSettings,
) -> tuple[Note, ...] | None:
    if (
        not isinstance(reply, Reply)
        or not isinstance(reply.detail, str)
        or not 0 < len(reply.detail) <= 120
    ):
        raise ValueError("Invalid peer response")
    if reply.notes is None:
        return None
    if not isinstance(reply.notes, tuple) or not reply.notes:
        raise ValueError("Peer response must leave a playable part")
    by_id = {note.event_id: note for note in original}
    for note in reply.notes:
        Note.model_validate(note)
        if note.start_tick + note.duration_ticks > settings.bar_ticks:
            raise ValueError("Peer response exceeds the current measure")
        previous = by_id.get(note.event_id)
        if previous is None:
            raise ValueError("Peer response must edit established material")
        if note == previous:
            continue
        if proposal.kind != "kick_pattern":
            raise ValueError("Space agreements may only omit notes")
        if (
            note.start_tick not in proposal.ticks
            or abs(note.start_tick - previous.start_tick) > PPQ // 6
            or (
                previous.start_tick % settings.pulse_ticks == 0
                and note.start_tick != previous.start_tick
            )
            or note.duration_ticks > previous.duration_ticks
            or note
            != previous.model_copy(
                update={
                    "start_tick": note.start_tick,
                    "duration_ticks": note.duration_ticks,
                }
            )
        ):
            raise ValueError("Kick alignment may not change harmony, channel, or anchor notes")
    remaining = {note.event_id for note in reply.notes}
    omitted = tuple(note for event_id, note in by_id.items() if event_id not in remaining)
    match proposal.kind:
        case "kick_pattern" if omitted:
            raise ValueError("Kick alignment may not remove low notes")
        case "lighter_drums" if any(
            not _ornament(note, proposal.ticks, settings.pulse_ticks) for note in omitted
        ):
            raise ValueError("Space agreements must preserve the drum pulse")
        case "reserve_space" if omitted:
            omitted_tick = omitted[0].start_tick
            if (
                omitted_tick == 0
                or not _near(omitted_tick, proposal.ticks)
                or any(note.event_id.endswith("-motif") for note in omitted)
                or omitted != tuple(note for note in original if note.start_tick == omitted_tick)
            ):
                raise ValueError("Space agreements may only skip an entire nearby chord attack")
    return validate_notes(bar, reply.notes) if reply.notes != original else None
