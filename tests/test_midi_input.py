from queue import Empty, Queue
from time import monotonic

from band.midi_input import MidiInput


class Driver:
    def __init__(self, names):
        self.names = names
        self.messages = Queue()
        self.opened = []
        self.rotate_on_open = False

    def backend(self):
        driver = self

        class Backend:
            port = None

            def get_current_api(self):
                return -1

            def get_ports(self):
                return list(driver.names)

            def open_port(self, index):
                if driver.rotate_on_open:
                    driver.names = tuple(reversed(driver.names))
                    driver.rotate_on_open = False
                self.port = driver.names[index]
                driver.opened.append(self.port)

            def is_port_open(self):
                return self.port is not None

            def close_port(self):
                self.port = None

            def get_message(self):
                try:
                    return driver.messages.get_nowait(), 0.0
                except Empty:
                    return None

        return Backend()


def _expect(statuses, predicate):
    deadline = monotonic() + 3
    while (remaining := deadline - monotonic()) > 0:
        status = statuses.get(timeout=remaining)
        if predicate(status):
            return status
    raise AssertionError("MIDI status never matched")


def test_port_discovery_filters_messages_and_reconnects_the_selected_source():
    statuses, received = Queue(), Queue()
    driver = Driver(("Keys", "Other"))
    device = MidiInput(
        lambda *parts: received.put(parts),
        statuses.put,
        backend_factory=driver.backend,
        poll_interval=0.015,
    )
    try:
        device.start()
        initial = _expect(statuses, lambda s: len(s["ports"]) == 2)
        assert initial["connection"] == "disabled"
        keys = next(p["id"] for p in initial["ports"] if p["name"] == "Keys")
        driver.rotate_on_open = True
        device.select(keys)
        _expect(statuses, lambda s: s["connection"] == "connected")
        assert driver.opened == ["Other", "Keys"]

        driver.messages.put([0xB0, 1, 64])
        driver.messages.put([0x90, 64])
        driver.messages.put([0x90, 64, 85])
        driver.messages.put([0xB0, 64, 127])
        driver.messages.put([0x90, 64, 0])
        assert [received.get(timeout=2) for _ in range(3)] == [
            (keys, (0x90, 64, 85)),
            (keys, (0xB0, 64, 127)),
            (keys, (0x90, 64, 0)),
        ]

        driver.names = ("Keys", "Keys", "Other")
        ambiguous = _expect(statuses, lambda s: s["connection"] == "waiting" and s["error"])
        assert ambiguous["selected"] == keys
        assert [port["name"] for port in ambiguous["ports"]] == ["Other"]

        driver.names = ("Keys", "Other")
        restored = _expect(statuses, lambda s: s["connection"] == "connected")
        assert restored["selected"] == keys
        assert driver.opened[-1] == "Keys"

        device.select(None)
        disabled = _expect(statuses, lambda s: s["connection"] == "disabled")
        assert disabled["selected"] is None
    finally:
        device.close()


def test_missing_midi_backend_is_reported_by_the_worker():
    def unavailable():
        raise ImportError("Simulated missing driver")

    statuses = Queue()
    device = MidiInput(lambda *_: None, statuses.put, backend_factory=unavailable)
    try:
        device.start()
        status = statuses.get(timeout=2)
        assert status["connection"] == "unavailable"
        assert status["ports"] == []
        assert status["error"]
    finally:
        device.close()
