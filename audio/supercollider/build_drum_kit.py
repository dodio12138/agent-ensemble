"""Render the installed GeneralUser GS Room kit to local, velocity-layered one shots."""

import math
import subprocess
import sys
import wave
from array import array
from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack

PITCHES = range(35, 60)
LAYERS = (60, 104)
RATE = 44100
SPACING = 3
TICKS_PER_SECOND = 960
CHANNELS = 2


def trim_tail(pitch: int, sample: array) -> array:
    # Give open metal and room signatures time to speak. The open hat has
    # a fixed lifetime so live playback can choke it without touching freed nodes.
    frames = int(
        RATE * (1.35 if pitch == 46 else 2.7 if pitch in {49, 51, 52, 55, 57, 59} else 1.1)
    )
    sample = sample[: frames * CHANNELS]
    if pitch != 46:
        end = next(
            (i for i in range(len(sample) - 1, -1, -1) if abs(sample[i]) >= 9),
            len(sample) - 1,
        )
        frames = min(frames, end // CHANNELS + 1 + RATE // 40)
        sample = sample[: frames * CHANNELS]
    return sample


def target_peak(pitch: int) -> float:
    if pitch in {35, 36}:
        return 0.36
    if pitch in {38, 40}:
        return 0.31
    if pitch in {42, 44, 46}:
        return 0.17
    if pitch in {49, 51, 52, 55, 57, 59}:
        return 0.22
    return 0.25


def build(fluid: Path, soundfont: Path, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    midi_path = directory.parent / "room-kit.mid"
    raw_path = directory.parent / "room-kit-render.wav"
    midi = MidiFile(type=0, ticks_per_beat=TICKS_PER_SECOND)
    track = MidiTrack(
        [
            MetaMessage("set_tempo", tempo=1_000_000),
            Message("program_change", channel=9, program=8),  # GeneralUser GS Room kit
        ]
    )
    midi.tracks.append(track)
    hit_length = int(2.8 * TICKS_PER_SECOND)
    gap = 0
    for pitch in PITCHES:
        for velocity in LAYERS:
            track.append(Message("note_on", channel=9, note=pitch, velocity=velocity, time=gap))
            track.append(Message("note_off", channel=9, note=pitch, time=hit_length))
            gap = SPACING * TICKS_PER_SECOND - hit_length
    track.append(MetaMessage("end_of_track", time=gap))
    midi.save(midi_path)
    subprocess.run(
        [
            str(fluid),
            "-n",
            "-i",
            "-q",
            "-g",
            "0.7",
            "-R",
            "0",
            "-C",
            "0",
            "-r",
            str(RATE),
            "-T",
            "wav",
            "-O",
            "s16",
            "-F",
            str(raw_path),
            str(soundfont),
            str(midi_path),
        ],
        check=True,
        timeout=45,
    )
    with wave.open(str(raw_path), "rb") as render:
        if (render.getnchannels(), render.getsampwidth(), render.getframerate()) != (
            CHANNELS,
            2,
            RATE,
        ):
            raise ValueError("Expected 44.1 kHz stereo, signed 16-bit FluidSynth output")
        for pitch in PITCHES:
            layers = []
            for _ in LAYERS:
                sample = array("h", render.readframes(RATE * SPACING))
                if sys.byteorder != "little":
                    sample.byteswap()
                layers.append(trim_tail(pitch, sample))
            peak = max(map(abs, layers[-1]), default=0)
            if peak < 20 or min(max(map(abs, sample), default=0) for sample in layers) < 20:
                raise ValueError(f"Missing drum sample {pitch}; Room kit render failed")
            gain = min(30.0, 32767 * target_peak(pitch) / peak)
            for index, sample in enumerate(layers):
                frames = len(sample) // CHANNELS
                fade_in, fade_out = RATE // 1200, RATE // 140
                audio = array(
                    "h",
                    (
                        min(
                            32767,
                            max(
                                -32768,
                                round(
                                    value
                                    * gain
                                    * math.sin(
                                        math.pi
                                        / 2
                                        * min(
                                            1.0,
                                            (i // CHANNELS) / fade_in,
                                            (frames - 1 - i // CHANNELS) / fade_out,
                                        )
                                    )
                                ),
                            ),
                        )
                        for i, value in enumerate(sample)
                    ),
                )
                if sys.byteorder != "little":
                    audio.byteswap()
                with wave.open(str(directory / f"{pitch}-{index}.wav"), "wb") as output:
                    output.setparams((CHANNELS, 2, RATE, 0, "NONE", "not compressed"))
                    output.writeframes(audio.tobytes())


if __name__ == "__main__":
    build(*map(Path, sys.argv[1:]))
