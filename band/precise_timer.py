"""Optional native timer that shortens the scheduler's wake-up jitter.

The C source lives in ``native/precise_timer.c``. It is compiled once into
``.runtime/native`` with the system C compiler. Without a compiler or on an
unsupported platform the scheduler keeps its pure-Python condition wait.
"""

from __future__ import annotations

import ctypes as c
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "native" / "precise_timer.c"
BUILD_DIR = ROOT / ".runtime" / "native"
LIBRARY = BUILD_DIR / (
    "libprecise_timer.dylib" if sys.platform == "darwin" else "libprecise_timer.so"
)
# Hand over to the native wait this long before a deadline, and spin for the last stretch.
# An ordinary thread's kernel timer can overshoot by ~0.5 ms, a promoted one by ~0.05 ms.
WINDOW_NS = 3_000_000
SPIN_NS = 1_000_000
PROMOTED_SPIN_NS = 300_000
_build_lock = threading.Lock()


def build(source: Path = SOURCE, library: Path = LIBRARY) -> Path | None:
    """Compile the timer if it is missing or older than its source."""
    with _build_lock:
        if library.is_file() and library.stat().st_mtime >= source.stat().st_mtime:
            return library
        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang")
        if compiler is None or sys.platform not in {"darwin", "linux"}:
            return None
        library.parent.mkdir(parents=True, exist_ok=True)
        partial = library.with_suffix(library.suffix + ".tmp")
        command = [compiler, "-O2", "-shared", "-fPIC", "-o", str(partial), str(source)]
        if sys.platform == "linux":
            command.append("-lpthread")
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("Could not compile the native timer: %s", error)
            partial.unlink(missing_ok=True)
            return None
        partial.replace(library)
        return library


class PreciseTimer:
    """ctypes wrapper. Calls release the GIL, so other threads keep running."""

    engine = "native"

    def __init__(self, library: Path) -> None:
        self._library = c.CDLL(str(library))
        self._library.ae_now_ns.restype = c.c_int64
        self._library.ae_now_ns.argtypes = ()
        self._library.ae_wait_until.restype = c.c_int64
        self._library.ae_wait_until.argtypes = (c.c_int64, c.c_int64)
        self._library.ae_promote_thread.restype = c.c_int
        self._library.ae_promote_thread.argtypes = (c.c_int64, c.c_int64)
        self.window_ns = WINDOW_NS
        self.spin_ns = SPIN_NS
        self.promoted = False
        offset = self._library.ae_now_ns() - time.perf_counter_ns()
        if abs(offset) > 5_000_000:
            raise RuntimeError(f"Native clock differs from perf_counter by {offset} ns")

    def now_ns(self) -> int:
        return self._library.ae_now_ns()

    def wait_until(self, deadline_ns: int) -> int:
        return self._library.ae_wait_until(deadline_ns, self.spin_ns)

    def promote(self) -> bool:
        """Raise the calling thread's scheduling class; safe to call from the timer thread."""
        self.promoted = bool(self._library.ae_promote_thread(2_000_000, 6_000_000))
        self.spin_ns = PROMOTED_SPIN_NS if self.promoted else SPIN_NS
        return self.promoted


def load(*, compile_missing: bool = True) -> PreciseTimer | None:
    if os.environ.get("ENSEMBLE_PYTHON_TIMER"):
        return None
    try:
        library = build() if compile_missing else (LIBRARY if LIBRARY.is_file() else None)
        return PreciseTimer(library) if library else None
    except (OSError, RuntimeError, AttributeError) as error:
        logger.warning("Native timer unavailable, using the Python wait: %s", error)
        return None


class LatenessStats:
    """Rolling wake-up lateness of scheduled note attacks."""

    def __init__(self, size: int = 512) -> None:
        self._samples: deque[int] = deque(maxlen=size)

    def add(self, late_ns: int) -> None:
        self._samples.append(late_ns)

    def clear(self) -> None:
        self._samples.clear()

    def snapshot(self) -> dict:
        samples = sorted(self._samples)
        if not samples:
            return {"samples": 0, "p50_us": None, "p99_us": None, "max_us": None}

        def percentile(fraction: float) -> int:
            return round(samples[min(len(samples) - 1, int(len(samples) * fraction))] / 1000)

        return {
            "samples": len(samples),
            "p50_us": percentile(0.5),
            "p99_us": percentile(0.99),
            "max_us": round(samples[-1] / 1000),
        }
