"""Start the isolated application on the loopback interface."""

import argparse
import os
import signal
import threading
import webbrowser
from pathlib import Path
from time import sleep

import uvicorn

from band.agents import ConductorAgents, endpoint_of
from band.api import ROOT, create_app
from band.conductor import Conductor
from band.local_model import LocalModel
from band.midi_output import MidiPortOutput
from band.native import NATIVE_LIBRARY, FluidOutput
from band.process_watchdog import sweep
from band.supercollider import LocalSuperCollider, SuperColliderOutput


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent Ensemble locally")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--open", action="store_true", help="Open the control console in a browser")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".runtime" / "data")
    parser.add_argument(
        "--native", action="store_true", help="Play audio locally through FluidSynth"
    )
    parser.add_argument(
        "--supercollider",
        action="store_true",
        help="Use the project-local SuperCollider, with the usual output as fallback",
    )
    parser.add_argument(
        "--soundfont", type=Path, default=ROOT / "soundfonts" / "GeneralUser-GS.sf2"
    )
    parser.add_argument(
        "--fluid-library",
        default=str(ROOT / ".runtime" / "fluidsynth" / "lib" / NATIVE_LIBRARY),
    )
    parser.add_argument("--audio-driver", help="Override the FluidSynth audio driver")
    parser.add_argument(
        "--rules-only", action="store_true", help="Use only built-in conducting rules"
    )
    parser.add_argument(
        "--logic",
        action="store_true",
        help="Play through Logic Pro (or any app) via the 'Agent Ensemble' virtual MIDI port",
    )
    args = parser.parse_args()
    audio_factory = None
    if args.logic:

        def audio_factory() -> MidiPortOutput:
            return MidiPortOutput()

    elif args.native:
        if not args.soundfont.is_file():
            parser.error(f"SoundFont missing: {args.soundfont}. Run scripts/setup-native first.")

        def audio_factory() -> FluidOutput:
            return FluidOutput(args.fluid_library, str(args.soundfont), args.audio_driver)

    model_process = LocalModel(ROOT / ".runtime" / "llm", args.state_dir)
    # Clear helpers that a killed or crashed earlier run left running.
    sweep([model_process.executable, ROOT / ".runtime" / "supercollider" / "scsynth"])
    sc_directory = ROOT / ".runtime" / "supercollider"
    sc_process = LocalSuperCollider(sc_directory, args.state_dir)
    url = os.environ.get("ENSEMBLE_LLM_BASE_URL")
    model = os.environ.get("ENSEMBLE_LLM_MODEL")
    key = os.environ.get("ENSEMBLE_LLM_API_KEY")
    try:
        local_label = "本机 Qwen"
        if args.rules_only:
            conductor = Conductor()
        elif url or model:
            try:
                conductor = Conductor(url, model, key)
            except ValueError as exc:
                parser.error(str(exc))
            local_label = f"外部 {model}"
        else:
            conductor = model_process.start()
        agents = ConductorAgents(
            conductor,
            ROOT / ".runtime" / "llm",
            local=endpoint_of(conductor),
            local_label=local_label,
        )
        sc_factory = None
        if args.supercollider and (sc_port := sc_process.start()) is not None:

            def sc_factory() -> SuperColliderOutput:
                return SuperColliderOutput(
                    sc_port,
                    sc_process.defs_dir,
                    server_alive=sc_process.is_running,
                    drum_kit_ready=sc_process.drum_kit_ready,
                )

        app = create_app(
            args.state_dir,
            audio_factory=audio_factory,
            agents=agents,
            sc_factory=sc_factory,
            prefer_sc=args.supercollider,
        )
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port))
        # Closing the terminal sends SIGHUP; shut down like Ctrl-C so the model process stops.
        signal.signal(signal.SIGHUP, lambda *_: setattr(server, "should_exit", True))
        if args.open:

            def open_when_ready() -> None:
                for _ in range(250):
                    if server.started:
                        webbrowser.open(f"http://127.0.0.1:{args.port}/")
                        return
                    sleep(0.04)

            threading.Thread(target=open_when_ready, daemon=True).start()
        server.run()
    finally:
        try:
            sc_process.close()
        finally:
            model_process.close()


if __name__ == "__main__":
    main()
