"""Exercise the real scheduler with recurring Agent faults for at least ten minutes."""

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from band.planner import BassAgent, Planner
from band.session import Session


class IntermittentBass(BassAgent):
    async def generate(self, context):
        if context.bar % 17 == 5:
            await asyncio.sleep(3)
        if context.bar % 23 == 8:
            raise RuntimeError("Injected bass planner failure")
        return await super().generate(context)


def check_log(path: Path, elapsed: float) -> dict:
    active = {}
    counts = {"drums": 0, "bass": 0, "piano": 0}
    late = []
    bars = 0
    fallback_bars = 0
    for line in path.read_text().splitlines():
        event = json.loads(line)
        match event["type"]:
            case "bar":
                assert event["bar"] == bars
                bars += 1
            case "plan" if event["status"] == "locked" and event["fallback_tracks"]:
                fallback_bars += 1
                assert event["fallback_tracks"] == ["bass"], event
            case "note_on":
                assert event["event_id"] not in active
                active[event["event_id"]] = event["track_id"]
                counts[event["track_id"]] += 1
                late.append(event["late_ns"])
            case "note_off":
                assert active.pop(event["event_id"]) == event["track_id"]
                if event["reason"] == "scheduled":
                    late.append(event["late_ns"])
            case "dropped_note" | "audio_error":
                raise AssertionError(event)
    assert not active and min(counts.values()) > 0
    assert bars >= int(elapsed / 2)
    if elapsed >= 20:
        assert fallback_bars >= 2
    late.sort()
    return {
        "seconds": round(elapsed, 3),
        "bars_started": bars,
        "note_on_by_track": counts,
        "bass_fallback_bars": fallback_bars,
        "unmatched_notes": len(active),
        "scheduler_late_ms": {
            name: round(late[math.ceil(len(late) * quantile) - 1] / 1e6, 3)
            for name, quantile in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))
        },
        "log": str(path),
    }


async def run(seconds: float, state_dir: Path) -> dict:
    session = Session(state_dir, planner=Planner(agents={"bass": IntermittentBass()}))
    log_path = state_dir / "sessions" / f"{session.id}.jsonl"
    await session.prime()
    session.scheduler.play()
    start = time.perf_counter()
    try:
        await asyncio.sleep(seconds)
    finally:
        session.scheduler.stop()
        elapsed = time.perf_counter() - start
        await session.close()
    return check_log(log_path, elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--state-dir", type=Path, help="Preserve JSONL audit artifacts in this folder")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.state_dir:
        result = asyncio.run(run(args.seconds, args.state_dir))
    else:
        with TemporaryDirectory() as path:
            result = asyncio.run(run(args.seconds, Path(path)))
        result.pop("log")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
