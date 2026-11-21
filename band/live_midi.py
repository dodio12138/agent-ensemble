"""Session-owned MIDI monitor and short-term musical listening context."""

import asyncio
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

from pydantic import Field

from band.midi_input import MidiInput
from band.music import PPQ, FrozenModel, PlayerPhrase, TimeSignature
from band.timing import Scheduler

Key = tuple[int, int]
MidiStatus = dict[str, Any]


class MidiMonitorSettings(FrozenModel):
    port: str | None = Field(default=None, min_length=1, max_length=128)
    volume: float = Field(default=0.8, ge=0, le=1)
    program: int = Field(default=0, ge=0, le=127)
    adaptive: bool = True


class MidiDevice(Protocol):
    def start(self) -> None: ...
    def select(self, port_id: str | None) -> None: ...
    def close(self) -> None: ...


MidiFactory = Callable[
    [Callable[[str, tuple[int, ...]], None], Callable[[MidiStatus], None]], MidiDevice
]


@dataclass(frozen=True, slots=True)
class Attack:
    tick: int
    bar_tick: int
    meter: TimeSignature
    pitch: int
    velocity: int


class LiveMidi:
    def __init__(self, scheduler: Scheduler, factory: MidiFactory = MidiInput):
        self.scheduler = scheduler
        self.factory = factory
        self.settings = MidiMonitorSettings()
        self.status: MidiStatus = {
            "ports": [],
            "selected": None,
            "connection": "disabled",
            "error": None,
        }
        self.device: MidiDevice | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listeners: set[asyncio.Event] = set()
        self.closed = False
        self.pressed: dict[Key, int] = {}
        self.sustained: dict[Key, int] = {}
        self.pedal: set[int] = set()
        self.voices: dict[int, str] = {}
        self.attacks: deque[Attack] = deque(maxlen=128)
        self.serial = 0
        self.next_phrase_update = 0.0

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        try:
            self.device = self.factory(self._from_device, self._from_status)
            self.device.start()
        except (OSError, RuntimeError):
            self.status.update(connection="unavailable", error="无法启动 MIDI 输入服务")

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Event]:
        wake = asyncio.Event()
        self.listeners.add(wake)
        try:
            yield wake
        finally:
            self.listeners.discard(wake)

    def _emit(self, event_type: str, **fields: Any) -> None:
        self.scheduler.record_midi(event_type, **fields)
        for wake in self.listeners:
            wake.set()

    def _from_device(self, port: str, packet: tuple[int, ...]) -> None:
        if self.loop and not self.closed:
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._message, port, packet, time.perf_counter_ns())

    def _from_status(self, status: MidiStatus) -> None:
        if self.loop and not self.closed:
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._update_status, status)

    def _update_status(self, status: MidiStatus) -> None:
        if self.closed:
            return
        if status["selected"] != self.settings.port:
            self.status = {**self.status, "ports": status["ports"]}
            return
        if status == self.status:
            return
        if self.status["connection"] == "connected" and status["connection"] != "connected":
            self.release("port_lost")
        self.status = status
        self._emit("midi_status", selected=status["selected"], connection=status["connection"])

    def snapshot(self) -> MidiStatus:
        return {
            **self.status,
            "selected": self.settings.port,
            "volume": self.settings.volume,
            "program": self.settings.program,
            "adaptive": self.settings.adaptive,
            "pressed": sorted({pitch for _, pitch in self.pressed}),
            "sounding": sorted(self.voices),
        }

    def configure(self, patch: dict) -> None:
        version = MidiMonitorSettings.model_validate({**self.settings.model_dump(), **patch})
        previous = self.settings
        self.settings = version
        if version.port != previous.port:
            self.release("port_changed")
            self.status = {
                **self.status,
                "selected": version.port,
                "connection": "waiting" if version.port else "disabled",
                "error": None,
            }
            if self.device:
                self.device.select(version.port)
        if not version.adaptive and previous.adaptive:
            self.clear_phrase()
        if version.volume != previous.volume:
            self._emit("midi_level", volume=version.volume)
        if version != previous:
            self._emit(
                "midi_settings",
                selected=version.port,
                volume=version.volume,
                program=version.program,
                adaptive=version.adaptive,
            )

    def _message(self, port: str, packet: tuple[int, ...], received: int) -> None:
        if self.closed or port != self.settings.port or self.status["connection"] != "connected":
            return
        if len(packet) != 3 or not all(0 <= value <= 127 for value in packet[1:]):
            return
        status, note, value = packet
        channel = status & 0x0F
        match status & 0xF0:
            case 0x90 if value:
                self._attack((channel, note), value, received)
            case 0x80 | 0x90:
                self._key_up((channel, note))
            case 0xB0:
                self._control(channel, note, value)

    def _attack(self, key: Key, velocity: int, received: int) -> None:
        channel, pitch = key
        self.sustained.pop(key, None)
        self.pressed[key] = velocity
        self._release_pitch(pitch, reason="retrigger")
        self.serial += 1
        event_id = f"player-{self.serial}"
        self.voices[pitch] = event_id
        self._emit(
            "midi_on",
            event_id=event_id,
            pitch=pitch,
            velocity=velocity,
            volume=self.settings.volume,
            program=self.settings.program,
            source_channel=channel,
            received_ns=received,
        )
        transport, absolute, tick, settings = self.scheduler.midi_position()
        if transport == "playing" and self.settings.adaptive:
            self.attacks.append(Attack(absolute, tick, settings.meter, pitch, velocity))

    def _key_up(self, key: Key) -> None:
        velocity = self.pressed.pop(key, None)
        if velocity is None:
            return
        if key[0] in self.pedal:
            self.sustained[key] = velocity
        else:
            self._release_idle_pitch(key[1])

    def _release_idle_pitch(self, pitch: int) -> None:
        if not any(
            key in self.pressed or key in self.sustained
            for key in ((channel, pitch) for channel in range(16))
        ):
            self._release_pitch(pitch, reason="key_up")

    def _release_pitch(self, pitch: int, *, reason: str) -> None:
        if (event_id := self.voices.pop(pitch, None)) is not None:
            self._emit(
                "midi_off",
                event_id=event_id,
                pitch=pitch,
                velocity=0,
                reason=reason,
            )

    def _control(self, channel: int, controller: int, value: int) -> None:
        if controller == 64 and value >= 64:
            self.pedal.add(channel)
        elif controller in (64, 120, 121, 123):
            self.pedal.discard(channel)
            keys = [key for key in self.sustained if key[0] == channel]
            if controller in (120, 123):
                keys.extend(key for key in self.pressed if key[0] == channel)
                for key in keys:
                    self.pressed.pop(key, None)
            for key in keys:
                self.sustained.pop(key, None)
                self._release_idle_pitch(key[1])

    def release(self, reason: str) -> None:
        for pitch in tuple(self.voices):
            self._release_pitch(pitch, reason=reason)
        self.pressed.clear()
        self.sustained.clear()
        self.pedal.clear()
        self.clear_phrase()
        self._emit("midi_silence", reason=reason)

    def clear_phrase(self) -> None:
        self.attacks.clear()
        self.next_phrase_update = 0.0
        self.scheduler.update_player_phrase(None)

    def update_phrase(self) -> None:
        if not self.settings.adaptive or self.status["connection"] != "connected":
            return
        transport, position, _, settings = self.scheduler.midi_position()
        if transport != "playing":
            if self.attacks:
                self.clear_phrase()
            return
        now = time.perf_counter()
        if now < self.next_phrase_update:
            return
        self.next_phrase_update = now + 0.12
        recent = [
            attack for attack in self.attacks if 0 <= position - attack.tick <= settings.bar_ticks
        ]
        held = self.pressed | self.sustained
        if not recent and not held:
            self.scheduler.update_player_phrase(None)
            return
        pitch = median([attack.pitch for attack in recent] or [key[1] for key in held])
        strength = median([attack.velocity for attack in recent] or list(held.values()))
        subdivision = PPQ // 4
        accents = {
            (attack.tick + subdivision // 2) // subdivision * subdivision for attack in recent
        }
        offbeats = [
            min(
                settings.bar_ticks - 1,
                (attack.bar_tick + subdivision // 2) // subdivision * subdivision,
            )
            for attack in recent
            if attack.meter == settings.meter
        ]
        accent = next((tick for tick in reversed(offbeats) if tick % settings.pulse_ticks), None)
        self.scheduler.update_player_phrase(
            PlayerPhrase(
                pitch_register="low" if pitch < 57 else "high" if pitch >= 72 else "middle",
                activity="busy" if len(accents) >= 5 else "sparse",
                accent_tick=accent,
                velocity="strong" if strength >= 85 else "soft",
            )
        )

    async def close(self) -> None:
        self.closed = True
        self.release("session_closed")
        if self.device:
            await asyncio.to_thread(self.device.close)
