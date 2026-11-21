"""Ableton Link: share tempo, bar phase and start/stop with other apps on the network.

Link's clock is the same monotonic host clock as ``time.perf_counter_ns``. The bridge
polls the Link session from the asyncio loop and keeps a cached reference point, so
the scheduler thread can align each bar start with pure arithmetic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Protocol

from band.music import PPQ

if TYPE_CHECKING:
    from band.timing import Scheduler

logger = logging.getLogger(__name__)

# A running bar is nudged by at most this much, so drift correction stays inaudible.
MAX_NUDGE_NS = 20_000_000
MIN_NUDGE_NS = 500_000
NANOSECONDS_PER_MINUTE = 60_000_000_000


class LinkSession(Protocol):
    enabled: bool
    quantum: float
    tempo: float
    start_stop_sync_enabled: bool
    playing: bool

    @property
    def beat(self) -> float: ...

    @property
    def phase(self) -> float: ...

    @property
    def num_peers(self) -> int: ...


def default_factory(bpm: float) -> LinkSession:
    from aalink import Link

    return Link(bpm)


def link_available() -> bool:
    try:
        import aalink  # noqa: F401
    except ImportError:
        return False
    return True


class LinkBridge:
    def __init__(
        self,
        scheduler: Scheduler,
        on_transport: Callable[[Literal["play", "pause"]], None],
        *,
        factory: Callable[[float], LinkSession] = default_factory,
        clock: Callable[[], int] = time.perf_counter_ns,
        available: bool | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._on_transport = on_transport
        self._factory = factory
        self._clock = clock
        self.available = link_available() if available is None else available
        self._link: LinkSession | None = None
        self.start_stop = False
        self.error: str | None = None
        self._synced_bpm: int | None = None
        self._link_playing: bool | None = None
        # (local ns, beat, tempo, quantum) sampled together on the asyncio loop.
        self._reference: tuple[int, float, float, float] | None = None
        self._resync = False
        self.last_shift_ns = 0
        self.corrections = 0

    @property
    def enabled(self) -> bool:
        return self._link is not None

    def configure(self, *, enabled: bool | None = None, start_stop: bool | None = None) -> None:
        if start_stop is not None:
            self.start_stop = start_stop
            if self._link is not None:
                self._link.start_stop_sync_enabled = start_stop
                self._link_playing = self._link.playing if start_stop else None
        if enabled is True and self._link is None:
            self._open()
        elif enabled is False and self._link is not None:
            self.close()

    def _open(self) -> None:
        if not self.available:
            self.error = "未安装 Ableton Link 组件（aalink）"
            raise RuntimeError(self.error)
        settings, _ = self._scheduler.desired_configuration()
        try:
            link = self._factory(float(settings.bpm))
            link.quantum = self._quantum()
            link.start_stop_sync_enabled = self.start_stop
            link.enabled = True
        except Exception as error:
            self.error = f"无法启动 Link：{error}"
            logger.exception("Could not enable Ableton Link")
            raise RuntimeError(self.error) from error
        self._link = link
        self.error = None
        self._synced_bpm = None
        self._link_playing = link.playing if self.start_stop else None
        self._resync = True
        self._scheduler.phase_aligner = self.align
        self.poll()

    def close(self) -> None:
        link, self._link = self._link, None
        self._scheduler.phase_aligner = None
        self._reference = None
        if link is not None:
            link.enabled = False

    def _quantum(self) -> float:
        return self._scheduler.midi_position()[3].bar_ticks / PPQ

    def poll(self) -> None:
        """Exchange tempo and transport with the Link session. Runs on the event loop."""
        link = self._link
        if link is None:
            return
        transport, _, _, performing = self._scheduler.midi_position()
        quantum = performing.bar_ticks / PPQ
        if link.quantum != quantum:
            link.quantum = quantum
            self._resync = True
        settings, _ = self._scheduler.desired_configuration()
        desired = performing.bpm if settings.song else settings.bpm
        session_bpm = round(link.tempo)
        if self._synced_bpm is None:
            if link.num_peers:
                self._adopt(session_bpm, settings.song is not None, desired)
            else:
                link.tempo = float(desired)
                self._synced_bpm = desired
        elif desired != self._synced_bpm:
            link.tempo = float(desired)
            self._synced_bpm = desired
        elif session_bpm != self._synced_bpm:
            self._adopt(session_bpm, settings.song is not None, desired)
        now = self._clock()
        self._reference = (now, link.beat, link.tempo, quantum)
        if self.start_stop:
            remote = link.playing
            if remote != self._link_playing:
                self._link_playing = remote
                playing = transport == "playing"
                if remote and not playing:
                    self._on_transport("play")
                elif not remote and playing:
                    self._on_transport("pause")

    def _adopt(self, session_bpm: int, song: bool, desired: int) -> None:
        bpm = min(240, max(40, session_bpm))
        if song:
            # Song sections own their tempo; keep publishing ours instead.
            if self._link is not None:
                self._link.tempo = float(desired)
            self._synced_bpm = desired
            return
        if bpm != desired:
            self._scheduler.stage_settings({"bpm": bpm})
        self._synced_bpm = bpm
        self._resync = True

    def notify_transport(self, playing: bool) -> None:
        if self._link is not None and self.start_stop:
            self._link_playing = playing
            self._link.playing = playing

    def start_time(self) -> int | None:
        """Monotonic ns of the next shared downbeat, or None when Link is off."""
        link = self._link
        if link is None:
            return None
        now = self._clock()
        beat, tempo, quantum = link.beat, link.tempo, link.quantum
        beats_left = (quantum - beat % quantum) % quantum
        if beats_left > quantum - 0.02:
            beats_left = 0
        self._resync = False
        return now + round(beats_left * NANOSECONDS_PER_MINUTE / tempo)

    def align(self, start_ns: int, length_ns: int) -> int:
        """Return a corrected start for the next bar. Called by the scheduler thread."""
        reference = self._reference
        if reference is None or self._link is None:
            return start_ns
        ref_ns, ref_beat, tempo, quantum = reference
        beat_ns = NANOSECONDS_PER_MINUTE / tempo
        if abs(length_ns - quantum * beat_ns) > 0.01 * length_ns:
            # A tempo or meter handover is still under way; correct once both agree.
            return start_ns
        phase = (ref_beat + (start_ns - ref_ns) / beat_ns) % quantum
        error_beats = phase if phase < quantum / 2 else phase - quantum
        shift = -round(error_beats * beat_ns)
        if self._resync:
            # After joining, a meter change or a tempo handover: jump to the shared
            # downbeat, waiting for the next one rather than starting a bar in the past.
            self._resync = False
            if shift < -MAX_NUDGE_NS:
                shift += round(quantum * beat_ns)
        elif abs(shift) < MIN_NUDGE_NS:
            shift = 0
        else:
            shift = max(-MAX_NUDGE_NS, min(MAX_NUDGE_NS, shift))
        if shift:
            self.corrections += 1
            self.last_shift_ns = shift
        return start_ns + shift

    def snapshot(self) -> dict[str, Any]:
        link = self._link
        base = {
            "available": self.available,
            "enabled": link is not None,
            "start_stop": self.start_stop,
            "error": self.error,
        }
        if link is None:
            return base
        return {
            **base,
            "peers": link.num_peers,
            "tempo": round(link.tempo, 2),
            "beat": round(link.beat, 3),
            "phase": round(link.phase, 3),
            "quantum": link.quantum,
            "playing": link.playing,
            "corrections": self.corrections,
            "last_shift_ms": round(self.last_shift_ns / 1_000_000, 2),
        }
