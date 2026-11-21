"""Isolate native MIDI discovery and input from the session and playback threads."""

import logging
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from ctypes import CDLL, c_uint32, c_ulong
from functools import cache
from hashlib import blake2b
from math import isfinite
from threading import Condition, Thread, current_thread
from time import monotonic
from typing import Protocol

Status = dict[str, object]
Message = tuple[int, ...]
Endpoint = int | str
_READ_INTERVAL = 0.002
_READ_BATCH = 128
_CONTROL_MESSAGES = {64, 120, 121, 123}
_ALSA_ADDRESS = re.compile(r" [0-9]+:[0-9]+$")
logger = logging.getLogger(__name__)


class MidiBackend(Protocol):
    def get_current_api(self) -> int: ...

    def get_ports(self) -> list[str]: ...

    def open_port(self, port: int) -> None: ...

    def close_port(self) -> None: ...

    def is_port_open(self) -> bool: ...

    def get_message(self) -> tuple[list[int], float] | None: ...


def _native_backend() -> MidiBackend:
    import rtmidi

    return rtmidi.MidiIn(name="Agent Ensemble")


def _port_id(name: str) -> str:
    return "midi-" + blake2b(name.encode(), digest_size=12).hexdigest()


@cache
def _core_midi() -> CDLL:
    core = CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
    core.MIDIGetNumberOfSources.argtypes = []
    core.MIDIGetNumberOfSources.restype = c_ulong
    core.MIDIGetSource.argtypes = [c_ulong]
    core.MIDIGetSource.restype = c_uint32
    return core


def _core_sources() -> tuple[int, ...]:
    core = _core_midi()
    return tuple(core.MIDIGetSource(i) for i in range(core.MIDIGetNumberOfSources()))


def _catalog(names: Sequence[str]) -> tuple[list[dict[str, str]], dict[str, int], set[str]]:
    counts = Counter(names)
    ambiguous = {_port_id(name) for name, count in counts.items() if count > 1}
    indexed = {_port_id(name): index for index, name in enumerate(names) if counts[name] == 1}
    ports = sorted(
        ({"id": port, "name": names[index]} for port, index in indexed.items()),
        key=lambda entry: (entry["name"].casefold(), entry["id"]),
    )
    return ports, indexed, ambiguous


def _message(raw: Sequence[int]) -> Message | None:
    if len(raw) != 3:
        return None
    status, data1, data2 = raw
    if not (0x80 <= status <= 0xBF and 0 <= data1 <= 127 and 0 <= data2 <= 127):
        return None
    kind = status & 0xF0
    if kind in {0x80, 0x90} or (kind == 0xB0 and data1 in _CONTROL_MESSAGES):
        return status, data1, data2
    return None


def _close_port(port: MidiBackend) -> None:
    try:
        port.close_port()
    except Exception:
        logger.debug("Unable to close MIDI port", exc_info=True)


class MidiInput:
    """One explicitly selected input with polling, reconnect, and worker-owned native handles.

    Both callbacks run on this worker. Discovery uses display names because RtMidi only
    exposes a transient ordinal for each port. Same-name ports are excluded until a
    particular source can be identified unambiguously.
    """

    def __init__(
        self,
        on_message: Callable[[str, Message], None],
        on_status: Callable[[Status], None],
        *,
        poll_interval: float = 0.3,
        backend_factory: Callable[[], MidiBackend] | None = None,
    ):
        if not isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        self._on_message = on_message
        self._on_status = on_status
        self._poll_interval = poll_interval
        self._factory = backend_factory or _native_backend
        self._native = backend_factory is None
        self._condition = Condition()
        self._selected: str | None = None
        self._closed = False
        self._thread: Thread | None = None
        self._last_status: Status | None = None

    def start(self) -> None:
        with self._condition:
            if self._closed or self._thread is not None:
                return
            self._thread = Thread(target=self._run, name="ensemble-midi-input", daemon=True)
            self._thread.start()

    def select(self, port_id: str | None) -> None:
        with self._condition:
            if not self._closed and port_id != self._selected:
                self._selected = port_id
                self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify()
            worker = self._thread
        if worker is not None and worker is not current_thread():
            worker.join(timeout=1)

    def _publish(
        self,
        selected: str | None,
        ports: list[dict[str, str]],
        connection: str,
        error: str | None = None,
    ) -> None:
        status = {"ports": ports, "selected": selected, "connection": connection, "error": error}
        if status != self._last_status and self._current(selected):
            self._on_status(status)
            self._last_status = status

    def _current(self, selected: str | None) -> bool:
        with self._condition:
            return not self._closed and selected == self._selected

    def _sources(self, backend: MidiBackend) -> tuple[tuple[str, ...], tuple[Endpoint, ...]]:
        if self._native:
            import rtmidi

            api = backend.get_current_api()
            if sys.platform == "darwin" and api == rtmidi.API_MACOSX_CORE:
                # The same visible name can be removed and recreated within one poll.
                for _ in range(2):
                    before = _core_sources()
                    names = tuple(backend.get_ports())
                    if before == _core_sources() and len(names) == len(before) and all(before):
                        return names, before
                raise RuntimeError("CoreMIDI source list changed while scanning")
            if api == rtmidi.API_LINUX_ALSA:
                addresses = tuple(backend.get_ports())
                return tuple(_ALSA_ADDRESS.sub("", name) for name in addresses), addresses
        names = tuple(backend.get_ports())
        return names, names

    def _connect(self, selected: str) -> tuple[MidiBackend, Endpoint]:
        reader = self._factory()
        try:
            before = self._sources(reader)
            matches = [i for i, name in enumerate(before[0]) if _port_id(name) == selected]
            if len(matches) != 1:
                raise RuntimeError("MIDI port changed during discovery")
            reader.open_port(matches[0])
            if self._sources(reader) != before or not reader.is_port_open():
                raise RuntimeError("MIDI port changed during connection")
        except Exception:
            _close_port(reader)
            raise
        return reader, before[1][matches[0]]

    def _read(self, reader: MidiBackend, selected: str) -> None:
        for _ in range(_READ_BATCH):
            packet = reader.get_message()
            if packet is None:
                break
            if (message := _message(packet[0])) is not None and self._current(selected):
                self._on_message(selected, message)

    def _run(self) -> None:
        scanner: MidiBackend | None = None
        reader: MidiBackend | None = None
        connected: str | None = None
        endpoint: Endpoint | None = None
        next_scan = 0.0
        seen: str | None = None
        ports: list[dict[str, str]] = []
        try:
            while True:
                with self._condition:
                    if self._closed:
                        return
                    selected = self._selected
                now = monotonic()
                if now >= next_scan or seen != selected:
                    seen = selected
                    next_scan = now + self._poll_interval
                    try:
                        if scanner is None:
                            scanner = self._factory()
                        names, endpoints = self._sources(scanner)
                        ports, indexed, ambiguous = _catalog(names)
                        reader_open = reader is not None and reader.is_port_open()
                    except Exception:  # noqa: BLE001 - backend failure leaves the app usable
                        if reader is not None:
                            _close_port(reader)
                        if scanner is not None:
                            _close_port(scanner)
                        scanner = reader = None
                        connected = None
                        endpoint = None
                        ports = []
                        self._publish(selected, ports, "unavailable", "系统 MIDI 后端暂不可用")
                        next_scan = now + max(self._poll_interval, 1)
                    else:
                        if reader is not None and (
                            connected != selected
                            or connected not in indexed
                            or endpoints[indexed[connected]] != endpoint
                            or not reader_open
                        ):
                            _close_port(reader)
                            reader = None
                            if connected is not None and connected == selected:
                                self._publish(selected, ports, "waiting")
                            connected = None
                            endpoint = None
                        error = None
                        if selected in ambiguous:
                            error = "所选 MIDI 端口出现重名"
                        if selected is not None and selected in indexed and reader is None:
                            try:
                                reader, endpoint = self._connect(selected)
                                connected = selected
                            except Exception:  # noqa: BLE001 - hotplug may invalidate the port
                                error = "暂时无法打开所选 MIDI 输入"
                        connection = (
                            "disabled"
                            if selected is None
                            else "connected"
                            if reader is not None
                            else "waiting"
                        )
                        self._publish(selected, ports, connection, error)
                if reader is not None and connected is not None and self._current(connected):
                    try:
                        self._read(reader, connected)
                    except Exception:  # noqa: BLE001 - release any held notes on a failed read
                        _close_port(reader)
                        reader = None
                        connected = None
                        endpoint = None
                        self._publish(selected, ports, "waiting", "MIDI 输入已中断")
                timeout = max(0, next_scan - monotonic())
                if reader is not None:
                    timeout = min(timeout, _READ_INTERVAL)
                with self._condition:
                    self._condition.wait_for(
                        lambda seen=seen: self._closed or self._selected != seen,
                        timeout=timeout,
                    )
        finally:
            if reader is not None:
                _close_port(reader)
            if scanner is not None:
                _close_port(scanner)
