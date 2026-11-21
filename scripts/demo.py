"""Export a trio MIDI or check the isolated FluidSynth audio path."""

import argparse
import asyncio
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from mido import Message, MidiFile, MidiTrack

from band.api import ROOT
from band.midi import export_midi
from band.music import PPQ, TRACKS, MusicSettings, TrackSettings
from band.native import NATIVE_LIBRARY, FluidOutput
from band.planner import Planner

NATIVE = ROOT / ".runtime" / "fluidsynth"
SOUNDFONT = ROOT / "soundfonts" / "GeneralUser-GS.sf2"
CHORD = (60, 64, 67)


async def write_trio(path: Path) -> None:
    settings = MusicSettings()
    planner = Planner()
    tracks = {key: TrackSettings(program=track.program) for key, track in TRACKS.items()}
    plans = [await planner.plan(bar, settings, 0) for bar in range(len(settings.progression))]
    path.write_bytes(export_midi(settings, tracks, plans))


def play_chord(wave: Path | None) -> None:
    if not SOUNDFONT.is_file():
        raise SystemExit("Run scripts/setup-native first.")
    if wave:
        midi = MidiFile(type=0, ticks_per_beat=PPQ)
        track = MidiTrack([Message("note_on", note=pitch, velocity=68) for pitch in CHORD])
        track.extend(
            Message("note_off", note=pitch, time=PPQ if index == 0 else 0)
            for index, pitch in enumerate(CHORD)
        )
        midi.tracks.append(track)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "triad.mid"
            midi.save(path)
            subprocess.run([
                str(NATIVE / "bin" / "fluidsynth"), "-ni", "-F", str(wave),
                str(SOUNDFONT), str(path),
            ], check=True)
        return

    sink = FluidOutput(str(NATIVE / "lib" / NATIVE_LIBRARY), str(SOUNDFONT))
    try:
        for pitch in CHORD:
            sink.deliver({
                "type": "note_on", "track_id": "piano", "program": 0,
                "pitch": pitch, "velocity": 68, "volume": .5,
            })
        time.sleep(.65)
        for pitch in CHORD:
            sink.deliver({"type": "note_off", "track_id": "piano", "pitch": pitch})
        time.sleep(.25)
    finally:
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    export = subparsers.add_parser("export", help="Write the default eight-bar trio as MIDI")
    export.add_argument("file", type=Path)
    chord = subparsers.add_parser("chord", help="Play a C major chord through FluidSynth")
    chord.add_argument("--wav", type=Path, help="Render the chord to a WAV file")
    args = parser.parse_args()
    if args.action == "export":
        asyncio.run(write_trio(args.file))
    else:
        play_chord(args.wav)


if __name__ == "__main__":
    main()
