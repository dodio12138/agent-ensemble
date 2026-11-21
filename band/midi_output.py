"""Play the band through another app, such as Logic Pro, over a virtual MIDI port.

The band opens a port named "Agent Ensemble" and sends each musician on its own MIDI
channel (the same channels used for MIDI export), so each Logic track can play one
musician with Logic's own instruments. Timing stays with the band's scheduler: notes
are sent as they are due. Program changes are not sent; the sound is chosen in Logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from band.music import PLAYER_CHANNEL, TRACK_DEFINITIONS, slide_controls

PORT_NAME = "Agent Ensemble"
# Logic Pro treats MIDI volume (CC7) 90 as 0 dB; 127 is about +6 dB. Map the band's
# full volume to unity so six tracks summed together leave headroom instead of clipping.
UNITY_CC7 = 90
CHANNELS = {track: definition.channel for track, definition in TRACK_DEFINITIONS.items()}


class _Port(Protocol):
    def open_virtual_port(self, name: str) -> Any: ...
    def send_message(self, message: list[int]) -> None: ...
    def close_port(self) -> None: ...


def _default_port() -> _Port:
    import rtmidi

    return rtmidi.MidiOut()


# One virtual port per name, shared by every session, so Logic sees a single source.
_shared: dict[str, list[Any]] = {}


def _acquire(name: str, factory: Callable[[], _Port]) -> _Port:
    entry = _shared.get(name)
    if entry is None:
        port = factory()
        port.open_virtual_port(name)
        entry = _shared[name] = [port, 0]
    entry[1] += 1
    return entry[0]


def _release(name: str) -> None:
    entry = _shared.get(name)
    if entry is None:
        return
    entry[1] -= 1
    if entry[1] <= 0:
        del _shared[name]
        entry[0].close_port()


class MidiPortOutput:
    """An audio sink that turns band events into MIDI on a virtual port."""

    device = "logic"

    def __init__(self, name: str = PORT_NAME, factory: Callable[[], _Port] = _default_port):
        self.name = name
        self.port = _acquire(name, factory)
        self.volumes: dict[int, int] = {}
        self.held: dict[int, set[int]] = {}
        self.sliding: set[int] = set()

    def _send(self, *message: int) -> None:
        self.port.send_message(list(message))

    def _volume(self, channel: int, gain: float) -> None:
        value = max(0, min(127, round(gain * UNITY_CC7)))
        if self.volumes.get(channel) != value:
            self._send(0xB0 | channel, 7, value)
            self.volumes[channel] = value

    def _on(self, channel: int, pitch: int, velocity: int, volume: float | None) -> None:
        if volume is not None:
            self._volume(channel, volume)
        held = self.held.setdefault(channel, set())
        if pitch in held:  # A retriggered key: end the old note first.
            self._send(0x80 | channel, pitch, 0)
        held.add(pitch)
        self._send(0x90 | channel, pitch, max(1, min(127, velocity)))

    def _slide(self, channel: int, event: dict) -> None:
        slide_from = event.get("slide_from")
        if slide_from is None and channel not in self.sliding:
            return
        for control, value in slide_controls(slide_from, event.get("slide_ms")):
            self._send(0xB0 | channel, control, value)
        (self.sliding.add if slide_from is not None else self.sliding.discard)(channel)

    def _off(self, channel: int, pitch: int) -> None:
        held = self.held.get(channel)
        if held is not None and pitch in held:
            held.discard(pitch)
            self._send(0x80 | channel, pitch, 0)

    def _silence_channel(self, channel: int) -> None:
        for pitch in sorted(self.held.pop(channel, set())):
            self._send(0x80 | channel, pitch, 0)
        self._send(0xB0 | channel, 64, 0)  # sustain pedal up
        self._send(0xB0 | channel, 123, 0)  # all notes off

    def deliver(self, event: dict) -> None:
        match event["type"]:
            case "note_on":
                channel = CHANNELS[event["track_id"]]
                self._slide(channel, event)
                self._on(channel, event["pitch"], event["velocity"], event.get("volume"))
            case "note_off":
                self._off(CHANNELS[event["track_id"]], event["pitch"])
            case "silence":
                track = event.get("track_id")
                for channel in (CHANNELS[track],) if track else CHANNELS.values():
                    self._silence_channel(channel)
            case "midi_on":
                self._on(PLAYER_CHANNEL, event["pitch"], event["velocity"], event.get("volume"))
            case "midi_off":
                self._off(PLAYER_CHANNEL, event["pitch"])
            case "midi_silence":
                self._silence_channel(PLAYER_CHANNEL)
            case "midi_level":
                self._volume(PLAYER_CHANNEL, event["volume"])

    def silence_all(self) -> None:
        for channel in (*CHANNELS.values(), PLAYER_CHANNEL):
            self._silence_channel(channel)

    def close(self) -> None:
        try:
            self.silence_all()
        finally:
            _release(self.name)
