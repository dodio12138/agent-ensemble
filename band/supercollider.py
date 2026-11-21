"""Project-local scsynth supervision and nonblocking OSC audio output."""

from __future__ import annotations

import logging
import os
import socket
import struct
import subprocess
from collections.abc import Callable
from contextlib import suppress
from itertools import count
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from band.process_watchdog import release, watch

logger = logging.getLogger(__name__)

PROBE_INTERVAL = 0.10
REPLY_TIMEOUT = 0.34
SETUP_TIMEOUT = 0.70
RETRY_INTERVAL = 0.35
RESTART_DELAY = 2.0

BAND_LANES = ("drums", "bass", "piano", "rhythm_guitar", "lead_guitar", "synth")
LANES = (*BAND_LANES, "player")
SYNTHS = (
    "ae_kick",
    "ae_snare",
    "ae_hat",
    "ae_tom",
    "ae_bass",
    "ae_electric_bass",
    "ae_piano",
    "ae_epiano",
    "ae_vibes",
    "ae_organ",
    "ae_clean_guitar",
    "ae_lead_guitar",
    "ae_pad",
)
EXTRA_VOICES = {
    "rhythm_guitar": "ae_clean_guitar",
    "lead_guitar": "ae_lead_guitar",
    "synth": "ae_pad",
}
GUITAR_ARTICULATIONS = {"palm_mute": 1, "legato": 2, "harmonic": 3}
KICKS = {35, 36}
SNARES = {37, 38, 39, 40}
TOMS = {41, 43, 45, 47, 48, 50}
DRUM_KIT_PITCHES = range(35, 60)
DRUM_KIT_SPLIT = 83
DRUM_KIT_LAYER_VELOCITIES = (60, 104)
DRUM_BUFFER_START = 256
OPEN_HAT_CHOKE_SECONDS = 1.25
NODE_IDS = count(0x22000000)


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return encoded + bytes(4 - len(encoded) % 4)


def _message(address: str, *args: str | float) -> bytes:
    tags = ","
    data = bytearray()
    for arg in args:
        match arg:
            case str():
                tags += "s"
                data.extend(_string(arg))
            case int():
                tags += "i"
                data.extend(struct.pack(">i", arg))
            case float():
                tags += "f"
                data.extend(struct.pack(">f", arg))
    return _string(address) + _string(tags) + data


def _bundle(*messages: bytes) -> bytes:
    return (
        b"#bundle\0"
        + struct.pack(">Q", 1)
        + b"".join(struct.pack(">i", len(message)) + message for message in messages)
    )


def _read_string(packet: bytes, offset: int) -> tuple[str, int]:
    end = packet.index(0, offset)
    return packet[offset:end].decode("utf-8"), (end + 4) & ~3


def _drum_buffer(pitch: int, layer: int) -> int:
    return DRUM_BUFFER_START + (pitch - DRUM_KIT_PITCHES.start) * 2 + layer


class LocalSuperCollider:
    """Supervise a single private scsynth process for all sessions."""

    def __init__(self, directory: Path, log_directory: Path):
        self.directory = directory.resolve()
        self.log_directory = log_directory
        self.defs_dir = self.directory / "synthdefs"
        self._kit_dir = self.directory / "drum-kit"
        self._kit_loaded = False
        self._port: int | None = None
        self._stop = Event()
        self._worker: Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> int | None:
        if self._stop.is_set():
            return None
        if self._worker is not None:
            return self._port
        binary = self.directory / "scsynth"
        if not os.access(binary, os.X_OK) or not all(
            (self.defs_dir / f"{name}.scsyndef").is_file() for name in SYNTHS
        ):
            return None
        try:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                self._port = reservation.getsockname()[1]
        except OSError:
            logger.exception("Could not initialize local scsynth")
            return None
        args = [
            str(binary),
            "-u",
            str(self._port),
            "-B",
            "127.0.0.1",
            "-i",
            "0",
            "-o",
            "2",
            "-D",
            "0",
            "-R",
            "0",
            "-n",
            "2048",
            "-P",
            str(self.directory),
        ]
        if (plugins := self.directory / "plugins").is_dir():
            args.extend(("-U", str(plugins)))
        self._worker = Thread(target=self._run, args=(args,), name="local-scsynth", daemon=True)
        self._worker.start()
        return self._port

    def _run(self, args: list[str]) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"SC_SYNTHDEF_PATH", "SC_PLUGIN_PATH"}
        }
        while not self._stop.is_set():
            try:
                with (self.log_directory / "scsynth.log").open("ab") as log_file:
                    process = subprocess.Popen(
                        args,
                        cwd=self.directory,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    self._process = process
                    watcher = watch(process.pid, "scsynth")
                    try:
                        self._kit_loaded = self._load_drum_kit(process)
                        while process.poll() is None and not self._stop.wait(0.10):
                            pass
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=1)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                        release(watcher)
            except OSError:
                logger.exception("Could not run local scsynth")
            finally:
                self._process = None
                self._kit_loaded = False
            self._stop.wait(RESTART_DELAY)

    def _load_drum_kit(self, process: subprocess.Popen) -> bool:
        """Load one shared bank per server, before any hit uses its buffers."""
        entries = [
            (_drum_buffer(pitch, layer), self._kit_dir / f"{pitch}-{layer}.wav")
            for pitch in DRUM_KIT_PITCHES
            for layer in range(2)
        ]
        if not (self.defs_dir / "ae_drum_sample.scsyndef").is_file() or not all(
            path.is_file() for _, path in entries
        ):
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.10)
            sock.connect(("127.0.0.1", self._port))
            deadline = monotonic() + 6
            while monotonic() < deadline and not self._stop.is_set() and process.poll() is None:
                try:
                    sock.send(_message("/status"))
                    if sock.recv(8192).startswith(b"/status.reply\0"):
                        break
                except OSError:
                    self._stop.wait(0.04)
            else:
                return False
            sync_id = next(NODE_IDS)
            for start in range(0, len(entries), 10):
                sock.send(
                    _bundle(
                        *(
                            _message("/b_allocRead", buffer, str(path))
                            for buffer, path in entries[start : start + 10]
                        )
                    )
                )
            sock.send(_message("/sync", sync_id))
            deadline = monotonic() + 2
            while monotonic() < deadline and not self._stop.is_set() and process.poll() is None:
                with suppress(TimeoutError):
                    reply = sock.recv(8192)
                    if reply == _message("/synced", sync_id):
                        return True
                    if reply.startswith(b"/fail\0"):
                        logger.warning("SuperCollider could not load the local drum kit")
                        return False
        return False

    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def drum_kit_ready(self) -> bool:
        return self._kit_loaded and self.is_running()

    def close(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=2)


class SuperColliderOutput:
    """Own one session's node tree on a private loopback scsynth server."""

    def __init__(
        self,
        port: int,
        defs_dir: Path,
        *,
        clock: Callable[[], float] = monotonic,
        server_alive: Callable[[], bool] | None = None,
        drum_kit_ready: Callable[[], bool] | None = None,
    ):
        self._clock = clock
        self._server_alive = server_alive
        self._drum_kit_ready = drum_kit_ready
        self._defs = str(defs_dir.resolve())
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("127.0.0.1", port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._root = next(NODE_IDS)
        self._groups = {lane: next(NODE_IDS) for lane in LANES}
        self._voices: dict[str, tuple[int, str]] = {}
        self._open_hat: tuple[int, float] | None = None
        self._sync_ids = count(1)
        self._sync: int | None = None
        self._phase = "down"
        self._created_groups = False
        self._last_reply = float("-inf")
        self._next_probe = float("-inf")
        self._retry_at = float("-inf")
        self._setup_until = float("-inf")
        self._closed = False

    def _down(self, now: float) -> None:
        if self._phase != "down":
            self._retry_at = now + RETRY_INTERVAL
        self._phase = "down"
        self._sync = None
        self._last_reply = float("-inf")
        self._voices.clear()
        self._open_hat = None

    def _send(self, address: str, *args: str | float) -> None:
        self._socket.send(_message(address, *args))

    def _send_sync(self, phase: str, now: float, *packets: bytes) -> None:
        sync_id = next(self._sync_ids)
        self._socket.send(_bundle(*packets, _message("/sync", sync_id)))
        self._sync = sync_id
        self._phase = phase
        self._setup_until = now + SETUP_TIMEOUT

    def _create_groups(self, now: float) -> None:
        commands = []
        if self._created_groups:
            commands.append(_message("/n_free", self._root))
        commands.append(_message("/g_new", self._root, 0, 0))
        commands.extend(_message("/g_new", group, 1, self._root) for group in self._groups.values())
        self._send_sync("groups", now, *commands)
        self._created_groups = True

    def _receive(self, packet: bytes, now: float) -> None:
        address, offset = _read_string(packet, 0)
        tags, offset = _read_string(packet, offset)
        if address == "/status.reply" and tags.startswith(",iiiiif"):
            self._last_reply = now
        elif address == "/synced" and tags == ",i":
            (sync_id,) = struct.unpack_from(">i", packet, offset)
            if sync_id != self._sync:
                return
            if self._phase == "loading":
                self._create_groups(now)
            elif self._phase == "groups":
                self._phase = "ready"
                self._sync = None
        elif address == "/fail" and tags.startswith(",s"):
            command, _ = _read_string(packet, offset)
            if command in {"/d_loadDir", "/g_new", "/s_new"}:
                self._down(now)

    def poll(self) -> bool:
        """Poll status and setup acknowledgements without waiting for the server."""
        if self._closed:
            return False
        now = self._clock()
        if self._server_alive is not None and not self._server_alive():
            self._created_groups = False
            self._down(now)
            return False
        if self._phase == "ready" and now - self._last_reply >= REPLY_TIMEOUT:
            self._down(now)
        if self._phase in {"loading", "groups"} and now >= self._setup_until:
            self._down(now)
        try:
            if now >= self._next_probe:
                self._next_probe = now + PROBE_INTERVAL
                self._send("/status")
            for _ in range(32):
                try:
                    packet = self._socket.recv(8192)
                except BlockingIOError:
                    break
                try:
                    self._receive(packet, now)
                except (ValueError, struct.error):
                    continue
            if (
                self._phase == "down"
                and now >= self._retry_at
                and now - self._last_reply < REPLY_TIMEOUT
            ):
                self._send_sync("loading", now, _message("/d_loadDir", self._defs))
        except OSError:
            self._down(now)
        return self._phase == "ready"

    @staticmethod
    def _voice(lane: str, pitch: int, program: int) -> str:
        if lane == "drums":
            if pitch in KICKS:
                return "ae_kick"
            if pitch in SNARES:
                return "ae_snare"
            if pitch in TOMS:
                return "ae_tom"
            return "ae_hat"
        if lane == "bass":
            return "ae_electric_bass" if 33 <= program <= 39 else "ae_bass"
        if lane == "synth" and program == 48:
            return "ae_strings"
        if lane in EXTRA_VOICES:
            return EXTRA_VOICES[lane]
        if program in {4, 5, 6, 7}:
            return "ae_epiano"
        if 16 <= program <= 20:
            return "ae_organ"
        return "ae_vibes" if program in {9, 10, 11, 12, 13, 14} else "ae_piano"

    def _note_on(self, lane: str, event: dict) -> None:
        event_id = event["event_id"]
        self._note_off(event_id)
        node = next(NODE_IDS)
        pitch = event["pitch"]
        if (
            lane == "drums"
            and pitch in DRUM_KIT_PITCHES
            and self._drum_kit_ready is not None
            and self._drum_kit_ready()
        ):
            self._drum_hit(node, pitch, event)
            self._voices[event_id] = (node, lane)
            return
        articulation = ()
        if lane in {"rhythm_guitar", "lead_guitar"}:
            articulation = ("art", GUITAR_ARTICULATIONS.get(event.get("articulation"), 0))
        self._send(
            "/s_new",
            self._voice(lane, pitch, event.get("program", 0)),
            node,
            1,
            self._groups[lane],
            "freq",
            440.0 * 2 ** ((pitch - 69) / 12),
            "amp",
            float(event["velocity"]) / 127.0,
            "gain",
            float(event.get("volume", 1)),
            "gate",
            1.0,
            "out",
            0,
            *articulation,
        )
        self._voices[event_id] = (node, lane)

    def _drum_hit(self, node: int, pitch: int, event: dict) -> None:
        now = self._clock()
        if pitch in {42, 44, 46}:
            if self._open_hat and now < self._open_hat[1]:
                self._send("/n_set", self._open_hat[0], "gate", 0.0)
            self._open_hat = (node, now + OPEN_HAT_CHOKE_SECONDS) if pitch == 46 else None
        velocity = event["velocity"]
        layer = int(velocity >= DRUM_KIT_SPLIT)
        dynamics = min(1.25, (velocity / DRUM_KIT_LAYER_VELOCITIES[layer]) ** 0.70)
        self._send(
            "/s_new",
            "ae_drum_sample",
            node,
            1,
            self._groups["drums"],
            "bufnum",
            _drum_buffer(pitch, layer),
            "rate",
            1 + (node % 11 - 5) / 750,
            "amp",
            velocity / 127.0,
            "gain",
            float(event.get("volume", 1)) * dynamics,
            "openness",
            float(pitch in {42, 44, 46, 49, 51, 52, 55, 57, 59}),
        )

    def _note_off(self, event_id: str) -> None:
        if (voice := self._voices.pop(event_id, None)) and voice[1] != "drums":
            self._send("/n_set", voice[0], "gate", 0.0)

    def _silence_lanes(self, *lanes: str) -> None:
        if "drums" in lanes:
            self._open_hat = None
        self._voices = {
            event_id: voice for event_id, voice in self._voices.items() if voice[1] not in lanes
        }
        if self._created_groups:
            self._send("/g_freeAll", *(self._groups[lane] for lane in lanes))

    def deliver(self, event: dict) -> None:
        """Route sequenced band or keyboard events; leave scheduling to the caller."""
        if self._phase != "ready" or self._closed:
            raise OSError("SuperCollider is not connected")
        if self._server_alive is not None and not self._server_alive():
            self._created_groups = False
            self._down(self._clock())
            raise OSError("SuperCollider process has exited")
        try:
            match event["type"]:
                case "note_on":
                    self._note_on(event["track_id"], event)
                case "midi_on":
                    self._note_on("player", event)
                case "note_off" | "midi_off":
                    self._note_off(event["event_id"])
                case "silence":
                    lane = event.get("track_id")
                    self._silence_lanes(*((lane,) if lane else BAND_LANES))
                case "midi_silence":
                    self._silence_lanes("player")
                case "midi_level":
                    self._send("/n_set", self._groups["player"], "gain", float(event["volume"]))
        except OSError:
            self._down(self._clock())
            raise

    def silence_all(self) -> None:
        if not self._closed:
            with suppress(OSError):
                self._silence_lanes(*LANES)

    def close(self) -> None:
        if self._closed:
            return
        if self._created_groups:
            with suppress(OSError):
                self._send("/n_free", self._root)
        self._voices.clear()
        self._closed = True
        self._socket.close()
