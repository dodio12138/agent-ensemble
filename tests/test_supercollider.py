import socket
import struct
from time import sleep

from band.supercollider import (
    BAND_LANES,
    LocalSuperCollider,
    SuperColliderOutput,
    _message,
    _read_string,
)


def _unpack(packet):
    if packet.startswith(b"#bundle\0"):
        index = 16
        while index < len(packet):
            size = struct.unpack_from(">i", packet, index)[0]
            index += 4
            yield from _unpack(packet[index : index + size])
            index += size
        return
    address, index = _read_string(packet, 0)
    tags, index = _read_string(packet, index)
    args = []
    for tag in tags[1:]:
        if tag == "s":
            value, index = _read_string(packet, index)
        else:
            value = struct.unpack_from(">" + tag, packet, index)[0]
            index += 4
        args.append(value)
    yield address, args


class _Peer:
    def __init__(self, sock):
        self.sock = sock
        self.respond = True
        self.messages = []

    def pump(self):
        while True:
            try:
                packet, sender = self.sock.recvfrom(8192)
            except BlockingIOError:
                break
            for command, args in _unpack(packet):
                self.messages.append((command, args))
                if self.respond and command == "/status":
                    self.sock.sendto(_message("/status.reply", 1, 0, 0, 0, 8, 0.0), sender)
                if self.respond and command == "/sync":
                    self.sock.sendto(_message("/synced", args[0]), sender)

    def wait_for(self, message):
        for _ in range(50):
            self.pump()
            if message in self.messages:
                return
            sleep(0.001)
        raise AssertionError(f"OSC message never arrived: {message}")

    def wait_for_voice(self, patch):
        for _ in range(50):
            self.pump()
            for command, args in self.messages:
                if command == "/s_new" and args[0] == patch:
                    return args[1]
            sleep(0.001)
        raise AssertionError(f"Synth never instantiated: {patch}")

    def through_setup(self, output):
        for i in range(50):
            ready = output.poll()
            self.pump()
            if i < 3:
                assert not ready
            if ready:
                break
            sleep(0.001)
        assert ready


def test_sc_handshake_routes_keyboard_and_band_independently(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        peer = _Peer(sock)
        output = SuperColliderOutput(sock.getsockname()[1], tmp_path, clock=lambda: 0.0)
        try:
            peer.through_setup(output)
            groups = [args[0] for command, args in peer.messages if command == "/g_new"]
            assert len(groups) == len(set(groups)) == len(BAND_LANES) + 2
            piano = {
                "type": "note_on",
                "track_id": "piano",
                "event_id": "piano",
                "pitch": 60,
                "velocity": 100,
            }
            player = {
                "type": "midi_on",
                "event_id": "player",
                "pitch": 60,
                "velocity": 82,
                "program": 4,
            }
            output.deliver(piano)
            output.deliver(player)
            output.deliver({"type": "midi_level", "volume": 0.5})
            output.deliver({"type": "silence"})
            output.deliver({"type": "midi_off", "event_id": "player"})
            peer.wait_for(("/g_freeAll", groups[1:-1]))
            notes = [args for command, args in peer.messages if command == "/s_new"]
            assert notes[0][:4] == ["ae_piano", notes[0][1], 1, groups[3]]
            assert notes[1][:4] == ["ae_epiano", notes[1][1], 1, groups[-1]]
            assert ("/n_set", [groups[-1], "gain", 0.5]) in peer.messages
            peer.wait_for(("/n_set", [notes[1][1], "gate", 0.0]))
        finally:
            output.close()
        peer.wait_for(("/n_free", [groups[0]]))


def test_sc_modern_lanes_release_their_voices_independently(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        peer = _Peer(sock)
        output = SuperColliderOutput(sock.getsockname()[1], tmp_path, clock=lambda: 0.0)
        try:
            peer.through_setup(output)
            for track, program, patch in (
                ("bass", 33, "ae_electric_bass"),
                ("rhythm_guitar", 27, "ae_clean_guitar"),
                ("lead_guitar", 30, "ae_lead_guitar"),
                ("synth", 89, "ae_pad"),
            ):
                output.deliver(
                    {
                        "type": "note_on",
                        "track_id": track,
                        "event_id": track,
                        "pitch": 60,
                        "velocity": 70,
                        "program": program,
                    }
                )
                output.deliver({"type": "note_off", "event_id": track})
                node = peer.wait_for_voice(patch)
                peer.wait_for(("/n_set", [node, "gate", 0.0]))
            output.deliver({"type": "silence", "track_id": "synth"})
            groups = [args[0] for command, args in peer.messages if command == "/g_new"]
            peer.wait_for(("/g_freeAll", [groups[1 + BAND_LANES.index("synth")]]))
        finally:
            output.close()


def test_sc_loses_heartbeat_or_process_and_recovers_by_rehandshake(tmp_path):
    now = 0.0
    alive = True

    def clock():
        return now

    def is_alive():
        return alive

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        peer = _Peer(sock)
        output = SuperColliderOutput(
            sock.getsockname()[1], tmp_path, clock=clock, server_alive=is_alive
        )
        try:
            peer.through_setup(output)
            alive = False
            assert not output.poll()
            alive = True
            now = 0.5
            peer.through_setup(output)
            peer.respond = False
            now = 0.85
            assert not output.poll()
        finally:
            output.close()


def test_missing_local_runtime_remains_optional(tmp_path):
    server = LocalSuperCollider(tmp_path / "missing", tmp_path)
    assert server.start() is None
    assert not server.is_running()
    server.close()
