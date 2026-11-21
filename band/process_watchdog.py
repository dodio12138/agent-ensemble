"""Stop helper processes (model server, scsynth) if the band process dies uncleanly.

Each helper runs in its own session so Ctrl-C reaches only the band, which then stops
it. A SIGKILL or crash skips that cleanup; this tiny shell watcher covers it. It only
signals the child while that PID still runs the expected program.
"""

import logging
import os
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

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


def sweep(executables: list[Path]) -> list[int]:
    """Stop this project's helper processes that an earlier run left behind.

    A helper counts as left behind when its parent is PID 1: the band that started
    it is gone. Helpers of another running instance still have that band as their
    parent, so they are never touched. Returns the PIDs that were signalled.
    """
    paths = {str(candidate) for path in executables for candidate in (path, path.resolve())}
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not list processes; skipping the leftover-helper sweep")
        return []
    stopped = []
    for line in listing.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3 or not fields[0].isdigit() or fields[1] != "1":
            continue
        if any(fields[2] == path or fields[2].startswith(path + " ") for path in paths):
            with suppress(ProcessLookupError, PermissionError):
                os.kill(int(fields[0]), signal.SIGTERM)
                stopped.append(int(fields[0]))
    if stopped:
        logger.info("Stopped %d helper processes left by an earlier run", len(stopped))
    return stopped
