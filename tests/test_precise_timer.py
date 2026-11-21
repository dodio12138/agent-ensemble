import asyncio
import time

import pytest

from band.planner import Planner
from band.precise_timer import LatenessStats, load
from band.timing import Scheduler


def test_lateness_statistics_report_percentiles_in_microseconds():
    stats = LatenessStats(size=4)
    assert stats.snapshot()["p50_us"] is None
    for late in (9_000_000, 1_000, 2_000, 3_000, 4_000):
        stats.add(late)
    assert stats.snapshot() == {"samples": 4, "p50_us": 3, "p99_us": 4, "max_us": 4}


def test_native_timer_shares_the_scheduler_clock_and_wakes_on_time():
    timer = load()
    if timer is None:
        pytest.skip("No C compiler for the native timer")
    assert abs(timer.now_ns() - time.perf_counter_ns()) < 1_000_000
    deadline = time.perf_counter_ns() + 2_000_000
    timer.wait_until(deadline)
    assert time.perf_counter_ns() >= deadline


def test_threaded_scheduler_dispatches_through_the_native_timer():
    timer = load()
    if timer is None:
        pytest.skip("No C compiler for the native timer")
    planner = Planner()
    scheduler = Scheduler(planner, timer=timer)
    try:

        async def commit():
            for bar, settings, version in scheduler.planning_targets():
                scheduler.commit(await planner.plan(bar, settings, version))

        asyncio.run(commit())
        scheduler.stage_settings({"bpm": 240})
        asyncio.run(commit())
        scheduler.play()
        for _ in range(200):
            if scheduler.snapshot()["timing"]["samples"] >= 4:
                break
            time.sleep(0.01)
        timing = scheduler.snapshot()["timing"]
        assert timing["engine"] == "native"
        assert timing["samples"] >= 4
        # Generous bound: CI machines are noisy, but a wake-up should never be this late.
        assert timing["p50_us"] < 20_000
    finally:
        scheduler.close()


def test_fake_clocks_never_use_the_native_timer():
    timer = load()
    scheduler = Scheduler(Planner(), clock=lambda: 0, threaded=False, timer=timer)
    assert scheduler.snapshot()["timing"]["engine"] == "python"
    scheduler.close()
