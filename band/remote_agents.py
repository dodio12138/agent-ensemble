"""Distributed musicians: another process takes over one track over a WebSocket.

The server sends a ``plan`` request for each bar with the shared musical context.
The remote process answers with notes in bar-relative ticks before its deadline.
A late, invalid or disconnected reply falls back to the built-in rule agent for
that bar only; the global clock and the other musicians are never blocked.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from band.music import PPQ, TRACK_DEFINITIONS, Note, TrackId
from band.planner import AgentOutput, BarContext

PROTOCOL = "agent-ensemble.remote/1"
REMOTE_DEADLINE = 0.3
NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


def describe_context(track: TrackId, context: BarContext) -> dict[str, Any]:
    """The musical facts a remote player needs; everything is plain JSON."""
    settings = context.settings
    definition = TRACK_DEFINITIONS[track]
    numerator, denominator = settings.meter_parts

    def chord(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "symbol": value.symbol,
            "root": value.root,
            "bass": value.bass,
            "intervals": list(value.intervals),
            "root_name": NOTE_NAMES[value.root],
        }

    return {
        "track": track,
        "bar": context.bar,
        "bpm": settings.bpm,
        "meter": settings.meter,
        "numerator": numerator,
        "denominator": denominator,
        "ppq": PPQ,
        "bar_ticks": settings.bar_ticks,
        "pulse_ticks": settings.pulse_ticks,
        "density": settings.density,
        "seed": settings.seed,
        "lineup": settings.lineup,
        "style": settings.conductor.style,
        "intensity": settings.conductor.intensity,
        "chord": chord(context.chord),
        "prior_chord": chord(context.prior_chord),
        "next_chord": chord(context.next_chord),
        "section": context.section.role if context.section else None,
        "range": {"low": definition.min_pitch, "high": definition.max_pitch},
        "channel": definition.channel,
        "max_notes": definition.max_notes,
        "band": {
            "kicks": list(context.kicks),
            "drum_notes": context.drum_notes,
            "bass_notes": context.bass_notes,
            "bass_high": context.bass_high,
            "piano_onsets": list(context.piano_onsets),
        },
    }


def parse_notes(track: TrackId, context: BarContext, reply: dict[str, Any]) -> tuple[Note, ...]:
    raw = reply.get("notes")
    if not isinstance(raw, list):
        raise TypeError("Reply must contain a list of notes")
    definition = TRACK_DEFINITIONS[track]
    notes = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError("Each note must be an object")
        try:
            start = int(item["start_tick"])
            duration = int(item["duration_ticks"])
            pitch = int(item["pitch"])
            velocity = int(item.get("velocity", 90))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Notes need integer start_tick, duration_ticks and pitch") from None
        notes.append(
            Note(
                event_id=f"{track}-{context.bar}-remote-{index}",
                track_id=track,
                bar=context.bar,
                start_tick=start,
                duration_ticks=duration,
                pitch=pitch,
                velocity=velocity,
                channel=definition.channel,
            )
        )
    return tuple(notes)


@dataclass
class RemoteStats:
    requests: int = 0
    answered: int = 0
    late: int = 0
    invalid: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def record(self, latency_ms: float) -> None:
        self.answered += 1
        self.latencies_ms = [*self.latencies_ms[-31:], latency_ms]

    def snapshot(self) -> dict[str, Any]:
        recent = sorted(self.latencies_ms)
        return {
            "requests": self.requests,
            "answered": self.answered,
            "late": self.late,
            "invalid": self.invalid,
            "latency_ms": round(recent[len(recent) // 2], 1) if recent else None,
        }


class RemoteAgent:
    """InstrumentAgent proxy. The Planner applies ``deadline`` instead of its default."""

    def __init__(
        self,
        track: TrackId,
        name: str,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        deadline: float = REMOTE_DEADLINE,
    ) -> None:
        self.track = track
        self.name = name
        self.deadline = deadline
        self.connected_at = time.time()
        self.stats = RemoteStats()
        self._send = send
        self._waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.last_intent: str | None = None

    async def generate(self, context: BarContext) -> AgentOutput:
        request_id = uuid4().hex[:12]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = future
        self.stats.requests += 1
        started = time.perf_counter()
        try:
            await self._send(
                {
                    "type": "plan",
                    "request_id": request_id,
                    "deadline_ms": round(self.deadline * 1000),
                    "context": describe_context(self.track, context),
                }
            )
            reply = await future
        except asyncio.CancelledError:
            self.stats.late += 1
            raise
        finally:
            self._waiting.pop(request_id, None)
        try:
            notes = parse_notes(self.track, context, reply)
        except (TypeError, ValueError):
            self.stats.invalid += 1
            raise
        self.stats.record((time.perf_counter() - started) * 1000)
        intent = reply.get("intent")
        self.last_intent = str(intent)[:80] if intent else f"{self.name} 远程演奏"
        return AgentOutput(intent=self.last_intent, notes=notes)

    def resolve(self, message: dict[str, Any]) -> None:
        future = self._waiting.get(str(message.get("request_id")))
        if future is not None and not future.done():
            future.set_result(message)

    def disconnect(self) -> None:
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(ConnectionError("远程乐手已断开"))
        self._waiting.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "connected_at": self.connected_at,
            "deadline_ms": round(self.deadline * 1000),
            "last_intent": self.last_intent,
            **self.stats.snapshot(),
        }
