import time
from typing import ClassVar
from uuid import uuid4

import pytest

from band.midi_output import MidiPortOutput


class FakePort:
    opened: ClassVar[list[str]] = []

    def __init__(self):
        self.sent = []
        self.closed = False

    def open_virtual_port(self, name):
        FakePort.opened.append(name)

    def send_message(self, message):
        self.sent.append(tuple(message))

    def close_port(self):
        self.closed = True


def note(kind, track, pitch, velocity=90, volume=0.8):
    return {"type": kind, "track_id": track, "pitch": pitch, "velocity": velocity, "volume": volume}


def test_each_musician_plays_on_its_own_channel_with_volume_and_clean_stops():
    name = f"test-{uuid4().hex[:6]}"
    output = MidiPortOutput(name, factory=FakePort)
    port = output.port
    output.deliver(note("note_on", "bass", 40))
    output.deliver(note("note_on", "drums", 36, volume=0.5))
    output.deliver(note("note_on", "lead_guitar", 67))
    output.deliver(note("note_off", "bass", 40))
    # Volume maps to Logic's unity gain (CC7 90 = 0 dB), so 0.8 sits below 0 dB.
    assert port.sent[:2] == [(0xB1, 7, 72), (0x91, 40, 90)]  # bass on channel 2
    assert (0xB9, 7, 45) in port.sent and (0x99, 36, 90) in port.sent  # drums on channel 10
    assert (0x94, 67, 90) in port.sent  # lead guitar on channel 5
    assert (0x81, 40, 0) in port.sent
    # A mute ends the held guitar note and clears the channel; no program changes are sent.
    output.deliver({"type": "silence", "track_id": "lead_guitar"})
    assert (0x84, 67, 0) in port.sent and (0xB4, 123, 0) in port.sent
    assert not any(message[0] & 0xF0 == 0xC0 for message in port.sent)
    output.close()


def test_sessions_share_one_virtual_port():
    name = f"test-{uuid4().hex[:6]}"
    before = FakePort.opened.count(name)
    first = MidiPortOutput(name, factory=FakePort)
    second = MidiPortOutput(name, factory=FakePort)
    assert first.port is second.port and FakePort.opened.count(name) == before + 1
    first.close()
    assert not second.port.closed
    second.close()
    assert second.port.closed


def test_a_real_virtual_port_reaches_a_midi_listener():
    rtmidi = pytest.importorskip("rtmidi")
    name = f"Agent Ensemble test {uuid4().hex[:6]}"
    try:
        output = MidiPortOutput(name)
    except Exception as error:  # noqa: BLE001 - e.g. no ALSA sequencer on a CI runner
        pytest.skip(f"No virtual MIDI on this machine: {error}")
    listener = rtmidi.MidiIn()
    try:
        index = next((i for i, port in enumerate(listener.get_ports()) if name in port), None)
        if index is None:
            pytest.skip("The virtual port is not visible to MIDI listeners here")
        listener.open_port(index)
        listener.ignore_types(sysex=True, timing=True, active_sense=True)
        output.deliver(note("note_on", "piano", 60, velocity=100))
        output.deliver(note("note_off", "piano", 60))
        received = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(received) < 3:
            message = listener.get_message()
            if message:
                received.append(tuple(message[0]))
            else:
                time.sleep(0.01)
        assert (0x90, 60, 100) in received and (0x80, 60, 0) in received
    finally:
        listener.close_port()
        output.close()
