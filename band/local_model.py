"""Lifecycle of the isolated, loopback-only inference process."""

import logging
import os
import platform
import secrets
import socket
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from band.conductor import Conductor
from band.process_watchdog import release, watch

SERVER_VERSION = "b12242"
MODEL_NAME = "qwen2.5-1.5b-instruct-q4_k_m"
MODEL_REVISION = "91cad51"
MODEL_ID = f"{MODEL_NAME}@{MODEL_REVISION}"
logger = logging.getLogger(__name__)


class LocalModel:
    def __init__(self, directory: Path, log_directory: Path) -> None:
        self.directory = directory
        self.log_directory = log_directory
        self.executable = directory / f"llama-{SERVER_VERSION}" / "llama-server"
        self.weights = directory / f"{MODEL_NAME}.gguf"
        self.process: subprocess.Popen | None = None
        self.watchdog: subprocess.Popen | None = None

    def start(self) -> Conductor:
        if not self.executable.is_file() or not self.weights.is_file():
            return Conductor()

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        key = secrets.token_urlsafe(32)
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("LLAMA_") and name != "ENSEMBLE_LLM_API_KEY"
        }
        env["LLAMA_API_KEY"] = key
        threads = str(max(1, min(4, (os.cpu_count() or 2) // 2)))
        args = [
            str(self.executable),
            "--model",
            str(self.weights),
            "--alias",
            MODEL_ID,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            "4096",
            "--parallel",
            "1",
            "--threads",
            threads,
            "--threads-batch",
            threads,
            "--no-webui",
        ]
        if sys.platform != "darwin" or platform.machine() != "arm64":
            args.extend(("--n-gpu-layers", "0"))

        try:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            log_path = self.log_directory / "llama.log"
            with log_path.open("ab") as log:
                self.process = subprocess.Popen(
                    args,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            with suppress(OSError, AttributeError):
                os.setpriority(os.PRIO_PROCESS, self.process.pid, 5)
            self.watchdog = watch(self.process.pid, "llama-server")
        except OSError:
            logger.exception("Local model startup failed. Rules remain available")
            return Conductor()
        return Conductor(f"http://127.0.0.1:{port}/v1", MODEL_ID, key, phrase_generation=True)

    def close(self) -> None:
        release(self.watchdog)
        self.watchdog = None
        if self.process is None:
            return
        with suppress(ProcessLookupError):
            self.process.terminate()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                self.process.kill()
            self.process.wait()
        self.process = None
