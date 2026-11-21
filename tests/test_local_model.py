import os
import signal
import subprocess
import sys
import time

import pytest

from band.local_model import MODEL_NAME, SERVER_VERSION, LocalModel

PARENT = """
import sys, time
from pathlib import Path
from band.local_model import LocalModel
model = LocalModel(Path(sys.argv[1]), Path(sys.argv[2]))
model.start()
print(model.process.pid, flush=True)
time.sleep(60)
"""


def fake_install(directory):
    server = directory / f"llama-{SERVER_VERSION}" / "llama-server"
    server.parent.mkdir(parents=True)
    server.write_text("#!/bin/sh\nsleep 60\n")
    server.chmod(0o755)
    (directory / f"{MODEL_NAME}.gguf").write_bytes(b"")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reaped-but-unwaited child of PID 1 can linger briefly as a zombie.
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], check=False, capture_output=True, text=True
    )
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX process groups")
def test_model_server_stops_when_the_band_process_is_killed(tmp_path):
    fake_install(tmp_path / "llm")
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT, str(tmp_path / "llm"), str(tmp_path / "logs")],
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.getcwd(),
    )
    try:
        child = int(parent.stdout.readline())
        assert alive(child)
        parent.send_signal(signal.SIGKILL)
        parent.wait()
        deadline = time.monotonic() + 10
        while alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not alive(child), "the model server outlived the band process"
    finally:
        if parent.poll() is None:
            parent.kill()


def test_close_stops_the_model_and_its_watchdog(tmp_path):
    fake_install(tmp_path / "llm")
    model = LocalModel(tmp_path / "llm", tmp_path / "logs")
    model.start()
    process, watchdog = model.process, model.watchdog
    assert process is not None and watchdog is not None
    model.close()
    assert process.poll() is not None and watchdog.poll() is not None
