import asyncio

import pytest

from band.link import MAX_NUDGE_NS, NANOSECONDS_PER_MINUTE, LinkBridge
from band.planner import Planner
from band.timing import Scheduler


class Clock:
    now = 1_000_000_000

    def __call__(self):
        return self.now

    def step(self, scheduler):
        self.now = scheduler.next_deadline_ns()
        return scheduler.pump()


class FakeLink:
    """Stands in for aalink.Link; the beat advances with the shared test clock."""

    def __init__(self, clock, bpm):
        self.clock = clock
        self.tempo = bpm
        self.quantum = 4.0
        self.enabled = False
        self.start_stop_sync_enabled = False
        self.playing = False
        self.num_peers = 0
        self.origin = clock()

    @property
    def beat(self):
        return (self.clock() - self.origin) * self.tempo / NANOSECONDS_PER_MINUTE

    @property
    def phase(self):
        return self.beat % self.quantum


def fill(scheduler, planner):
    async def commit():
        for bar, settings, version in scheduler.planning_targets():
            scheduler.commit(await planner.plan(bar, settings, version))

    asyncio.run(commit())


@pytest.fixture
def rig():
    clock = Clock()
    planner = Planner()
    scheduler = Scheduler(planner, clock=clock, threaded=False)
    links = []
    transport = []

    def factory(bpm):
        links.append(FakeLink(clock, bpm))
        return links[-1]

    bridge = LinkBridge(scheduler, transport.append, factory=factory, clock=clock, available=True)
    yield clock, planner, scheduler, bridge, links, transport
    bridge.close()
    scheduler.close()


def test_link_publishes_local_tempo_and_adopts_peer_tempo(rig):
    _, _, scheduler, bridge, links, _ = rig
    bridge.configure(enabled=True)
    link = links[0]
    assert link.enabled and link.tempo == 120 and link.quantum == 4
    scheduler.stage_settings({"bpm": 132})
    bridge.poll()
    assert link.tempo == 132
    link.num_peers = 1
    link.tempo = 97.6
    bridge.poll()
    assert scheduler.desired_configuration()[0].bpm == 98
    assert bridge.snapshot()["peers"] == 1
    scheduler.stage_settings({"meter": "3/4"})
    bridge.poll()
    assert link.quantum == 3
    bridge.configure(enabled=False)
    assert not link.enabled and scheduler.phase_aligner is None


def test_play_waits_for_shared_downbeat_and_running_bars_are_nudged(rig):
    clock, planner, scheduler, bridge, links, _ = rig
    bridge.configure(enabled=True)
    link = links[0]
    beat_ns = NANOSECONDS_PER_MINUTE // 120
    clock.now += beat_ns * 5 // 2  # 2.5 beats into the shared bar
    bridge.poll()
    fill(scheduler, planner)
    start = bridge.start_time()
    assert start == clock.now + beat_ns * 3 // 2
    state = scheduler.play(start)
    assert state["timing"]["waiting_for_downbeat"]
    assert link.beat % 4 != 0
    # A peer pulls the shared timeline 5 ms ahead; the next bar starts earlier to follow.
    link.origin -= 5_000_000
    bridge.poll()
    while scheduler.snapshot()["current_bar"] < 1:
        fill(scheduler, planner)
        clock.step(scheduler)
    bars = [event for event in scheduler.events_after(0) if event["type"] == "bar"]
    assert bars[1]["due_ns"] - bars[0]["due_ns"] == 4 * beat_ns - 5_000_000
    assert bridge.snapshot()["last_shift_ms"] == -5.0
    # Large offsets are corrected gradually instead of jumping inside a running groove.
    link.origin -= 200_000_000
    bridge.poll()
    while scheduler.snapshot()["current_bar"] < 2:
        fill(scheduler, planner)
        clock.step(scheduler)
    bars = [event for event in scheduler.events_after(0) if event["type"] == "bar"]
    assert bars[2]["due_ns"] - bars[1]["due_ns"] == 4 * beat_ns - MAX_NUDGE_NS


def test_start_stop_sync_follows_peers_without_echo(rig):
    _, _, scheduler, bridge, links, transport = rig
    bridge.configure(enabled=True, start_stop=True)
    link = links[0]
    assert link.start_stop_sync_enabled
    link.playing = True
    bridge.poll()
    assert transport == ["play"]
    scheduler.play()
    bridge.notify_transport(True)
    bridge.poll()
    assert transport == ["play"]
    link.playing = False
    bridge.poll()
    assert transport == ["play", "pause"]


def test_missing_link_library_reports_an_error(tmp_path):
    scheduler = Scheduler(Planner(), threaded=False)
    bridge = LinkBridge(scheduler, lambda action: None, available=False)
    with pytest.raises(RuntimeError):
        bridge.configure(enabled=True)
    assert bridge.snapshot() == {
        "available": False,
        "enabled": False,
        "start_stop": False,
        "error": "未安装 Ableton Link 组件（aalink）",
    }
    scheduler.close()
