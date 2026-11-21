"""Render the new lead from the recorded model responses over the exact baseline backing."""

import json
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mido

from band.phrase_score import STEPS_PER_QUARTER

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
WORK = ROOT / ".runtime/qwen-phrases"
BASE_MIDI = {"cinematic": "Qwen-星际穿越风格", "folk": "Qwen-鲍勃迪伦风格"}
BAR = 4 * STEPS_PER_QUARTER
FORM = ((0, "a1"), (2, "a1"), (4, "a2"), (6, "b1"), (8, "b2"), (10, "a1"), (12, "a2"), (14, "a2"))


def lead_track(track, score, step):
    events = []
    channel = next(message.channel for message in track if message.type == "program_change")
    total = 0
    for message in track:
        total += message.time
        if message.type == "program_change" or (message.type == "control_change" and total == 0):
            events.append((total, 0, message))

    for bar, phrase in FORM:
        for onset, duration, pitch, velocity in score["phrases"][phrase]["notes"]:
            if bar == 0 and onset < BAR:
                # The original first bar is accompaniment only.
                continue
            start = bar * BAR + onset
            end = start + duration
            if score["excerpt"] == "folk":
                # The existing breath/bend gesture is applied within the model-written note.
                wheel = [(0, -17), (duration - 1, 0)]
                if duration > 6:
                    wheel.append((4, 0))
                if duration >= 16:
                    wheel.extend(
                        (i * 3 + 7, cents)
                        for i, cents in enumerate((3, -4, 4, -3))
                        if i * 3 + 7 < duration - 2
                    )
                for offset, cents in wheel:
                    events.append(
                        (
                            (start + offset) * step,
                            2,
                            mido.Message(
                                "pitchwheel", channel=channel, pitch=round(cents * 8192 / 200)
                            ),
                        )
                    )
                for offset, expression in ((0, 85), (duration // 4, 105), (duration * 3 // 4, 97)):
                    events.append(
                        (
                            (start + offset) * step,
                            2,
                            mido.Message(
                                "control_change", channel=channel, control=11, value=expression
                            ),
                        )
                    )
            events.append(
                (
                    start * step,
                    3,
                    mido.Message("note_on", channel=channel, note=pitch, velocity=velocity),
                )
            )
            events.append((end * step, 1, mido.Message("note_off", channel=channel, note=pitch)))

    result = mido.MidiTrack([mido.MetaMessage("track_name", name=track.name)])
    cursor = 0
    active = None
    durations = Counter()
    for when, _, message in sorted(events, key=lambda entry: entry[:2]):
        if message.type == "note_on":
            if active is not None:
                raise ValueError("The lead contains overlapping notes")
            active = when
        elif message.type == "note_off":
            if active is None:
                raise ValueError("Missing note-on")
            durations[(when - active) // step] += 1
            active = None
        result.append(message.copy(time=when - cursor))
        cursor = when
    if cursor > total or active is not None:
        raise ValueError("The lead extends past the baseline")
    result.append(mido.MetaMessage("end_of_track", time=total - cursor))
    return result, durations


def render(score):
    name = BASE_MIDI[score["excerpt"]]
    baseline = mido.MidiFile(OUTPUT / f"{name}.mid")
    updated = mido.MidiFile(type=baseline.type, ticks_per_beat=baseline.ticks_per_beat)
    step, remainder = divmod(updated.ticks_per_beat, STEPS_PER_QUARTER)
    if remainder:
        raise ValueError("Unexpected MIDI resolution")
    for track in baseline.tracks:
        if track.name.endswith("/ lead"):
            lead, durations = lead_track(track, score, step)
            updated.tracks.append(lead)
        else:
            updated.tracks.append(track)
    midi = OUTPUT / f"{name}-乐句版.mid"
    updated.save(midi)
    check = mido.MidiFile(midi)
    if [track for track in check.tracks if not track.name.endswith("/ lead")] != [
        track for track in baseline.tracks if not track.name.endswith("/ lead")
    ]:
        raise ValueError("The comparison changed the backing tracks")
    with tempfile.TemporaryDirectory(dir=WORK) as staging:
        wav = Path(staging) / "mix.wav"
        subprocess.run(
            [
                str(ROOT / ".runtime/fluidsynth/bin/fluidsynth"),
                "-ni",
                "-q",
                "-g",
                ".66",
                "-C",
                "0",
                "-r",
                "48000",
                "-F",
                str(wav),
                "-T",
                "wav",
                "-O",
                "s16",
                str(ROOT / "soundfonts/GeneralUser-GS.sf2"),
                str(midi),
            ],
            check=True,
        )
        highpass = 58 if score["excerpt"] == "folk" else 28
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(wav),
                "-af",
                f"highpass=f={highpass},volume=17dB,alimiter=limit=0.9:level=false",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(OUTPUT / f"{name}-乐句版.mp3"),
            ],
            check=True,
        )
    print(name, round(updated.length, 2), dict(durations), flush=True)


def main():
    decision_file = WORK / "decisions.json"
    report = decision_file.read_text()
    scores = json.loads(report)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(render, scores))
    (OUTPUT / "Qwen-乐句原始决策.json").write_text(report)


if __name__ == "__main__":
    main()
