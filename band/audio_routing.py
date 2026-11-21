"""Route already scheduled notes without involving the playback clock in I/O."""

from collections.abc import Callable
from contextlib import suppress
from time import monotonic
from typing import Literal, Protocol

AudioSource = Literal["browser", "native", "supercollider"]
AUDIO_EVENTS = frozenset(
    {
        "note_on",
        "note_off",
        "silence",
        "midi_on",
        "midi_off",
        "midi_silence",
        "midi_level",
    }
)


class AudioSink(Protocol):
    def deliver(self, event: dict) -> None: ...
    def silence_all(self) -> None: ...
    def close(self) -> None: ...


class SuperColliderSink(AudioSink, Protocol):
    def poll(self) -> bool: ...


class AudioRouter:
    """Own the two local synth clients and switch at a sequenced event boundary."""

    def __init__(
        self,
        fallback: AudioSink | None,
        supercollider: SuperColliderSink | None,
        *,
        prefer_supercollider: bool = False,
    ) -> None:
        self.fallback = fallback
        self.supercollider = supercollider
        self.fallback_source: AudioSource = "native" if fallback else "browser"
        self.preferred: AudioSource = (
            "supercollider" if prefer_supercollider else self.fallback_source
        )
        self.active: AudioSource = self.fallback_source
        reason = ("starting" if supercollider else "unavailable") if prefer_supercollider else None
        self.status: tuple[AudioSource, str | None] = (self.active, reason)
        self.start_deadline = monotonic() + 3.0
        self.retry_at = 0.0

    def poll(self, switch: Callable[[AudioSource, str | None], None]) -> None:
        if not self.supercollider or monotonic() < self.retry_at:
            return
        try:
            ready = self.supercollider.poll()
        except (OSError, RuntimeError, ValueError):
            if self.status[0] == "supercollider":
                self._unavailable(switch)
            elif monotonic() >= self.start_deadline and self.status[1] == "starting":
                self._publish(switch, self.fallback_source, "unavailable")
            return
        if ready:
            self._publish(switch, "supercollider", None)
        elif self.status[0] == "supercollider":
            self._unavailable(switch)
        elif monotonic() >= self.start_deadline and self.status[1] == "starting":
            self._publish(switch, self.fallback_source, "unavailable")

    def _unavailable(self, switch: Callable[[AudioSource, str | None], None]) -> None:
        self.retry_at = monotonic() + 0.15
        self._publish(switch, self.fallback_source, "disconnected")

    def _publish(
        self,
        switch: Callable[[AudioSource, str | None], None],
        route: AudioSource,
        reason: str | None,
    ) -> None:
        status = (route, reason)
        if status != self.status:
            self.status = status
            switch(route, reason)

    def _sink(self, route: AudioSource) -> AudioSink | None:
        if route == "supercollider":
            return self.supercollider
        return self.fallback if route == "native" else None

    def deliver(self, event: dict, switch: Callable[[AudioSource, str | None], None]) -> None:
        event_type = event["type"]
        if event_type == "audio_route":
            route = event["audio_output"]
            old = self._sink(self.active)
            if route != self.active and old:
                with suppress(OSError, RuntimeError, ValueError):
                    old.silence_all()
            self.active = route
        elif event_type not in AUDIO_EVENTS:
            return

        sink = self._sink(self.active)
        if not sink:
            return
        try:
            if event_type == "audio_route":
                for held in event["resume"]:
                    sink.deliver(held)
            else:
                sink.deliver(event)
        except (OSError, RuntimeError, ValueError):
            if self.active == "supercollider":
                self._unavailable(switch)
            raise

    def close(self) -> None:
        try:
            if self.supercollider:
                self.supercollider.close()
        finally:
            if self.fallback:
                self.fallback.close()
