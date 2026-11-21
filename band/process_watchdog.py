"""Stop helper processes (model server, scsynth) if the band process dies uncleanly.

Each helper runs in its own session so Ctrl-C reaches only the band, which then stops
it. A SIGKILL or crash skips that cleanup; this tiny shell watcher covers it. It only
signals the child while that PID still runs the expected program.
"""

import logging
import os
import subprocess
from contextlib import suppress

logger = logging.getLogger(__name__)

SCRIPT = """
while kill -0 "$1" 2>/dev/null; do sleep 2; done
case "$(ps -p "$2" -o command= 2>/dev/null)" in
    *"$3"*) kill "$2" 2>/dev/null; sleep 4; kill -9 "$2" 2>/dev/null ;;
esac
"""


def watch(child: int, program: str) -> subprocess.Popen | None:
    """Start a watcher that stops `child` once this process is gone."""
    try:
        return subprocess.Popen(
            ["/bin/sh", "-c", SCRIPT, "band-watchdog", str(os.getpid()), str(child), program],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.warning("Could not start a watchdog for %s; normal shutdown still stops it", program)
        return None


def release(watcher: subprocess.Popen | None) -> None:
    if watcher is None:
        return
    with suppress(ProcessLookupError):
        watcher.kill()
    watcher.wait()
