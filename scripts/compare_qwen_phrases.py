"""Replay the two original prompts with Qwen-authored lead phrases for an A/B comparison."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

import httpx2

from band.local_model import LocalModel
from band.phrase_generation import (
    PHRASE_PROMPT,
    chord_pitches,
    decode_phrase,
    phrase_grammar,
    song_scale,
)
from band.phrase_score import STEPS_PER_QUARTER, phrase_issues

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
BASELINE = OUTPUT / "Qwen-两段编排原始输出.json"
WORK = ROOT / ".runtime/qwen-phrases"

STYLES = {
    "cinematic": (
        ("C", "Em", "G", "C"),
        (63, 79),
        "Pipe organ with bowed strings. Spacious cinematic instrumental. Hold across a sigh.",
    ),
    "folk": (
        ("C#m", "D", "G#dim", "A"),
        (69, 83),
        "Harmonica with fingerpicked guitar. Play like a singer taking breaths between lines.",
    ),
}
PHRASES = {
    "a1": "Introduce a singable idea, with a focal note and air around it.",
    "a2": "Answer that idea in a more settled register. Contrast the rhythm of the opening.",
    "b1": "Open up the sound. Change the way the line enters, then recall a few pitches.",
    "b2": "Resolve the response and leave audible space for the first theme to return.",
}
BAR = 4 * STEPS_PER_QUARTER


def preference(notes, previous):
    pitches = [row[2] for row in notes]
    durations = {row[1] for row in notes}
    velocities = [row[3] for row in notes]
    timing = [row[:2] for row in notes]
    return (
        len(set(pitches)) >= 3,
        not previous or timing != [row[:2] for row in previous],
        max(velocities) - min(velocities) >= 7,
        len(durations) >= 3,
        len(set(pitches)),
    )


async def compose(style, item):
    chords, pitch_range, sound = STYLES[style]
    original = item["model_plan"]
    scale = song_scale(original["key"], original["mode"])
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{style}-", dir=WORK) as log_dir:
        local = LocalModel(ROOT / ".runtime/llm", Path(log_dir))
        model = local.start()
        try:
            if not model.endpoint:
                raise RuntimeError("Local Qwen is unavailable")
            async with httpx2.AsyncClient(
                headers=model._headers,
                timeout=45,
                trust_env=False,
            ) as client:
                async with asyncio.timeout(30):
                    while True:
                        try:
                            health = model.endpoint.removesuffix("/v1/chat/completions") + "/health"
                            if (await client.get(health)).status_code == 200:
                                break
                        except httpx2.ConnectError:
                            pass
                        await asyncio.sleep(0.25)
                phrases = {}
                prior = None
                for index, (label, role) in enumerate(PHRASES.items()):
                    start = 2 * (index % 2)
                    active = chords[start], chords[start + 1]
                    following = chords[(start + 2) % 4]
                    context = {
                        "instruction": item["user_instruction"],
                        "instrument_and_approach": sound,
                        "phrase_role": role,
                        "units_per_bar": 32,
                        "previous_chord": chords[(start - 1) % 4],
                        "bar_1_chord": active[0],
                        "bar_1_chord_notes": chord_pitches(active[0], pitch_range),
                        "bar_2_chord": active[1],
                        "next_chord": following,
                        "next_chord_notes": chord_pitches(following, pitch_range),
                        "cadence_choices": chord_pitches(active[1], pitch_range),
                        "prior_phrase_pitches": [row[2] for row in prior] if prior else [],
                    }
                    grammar = phrase_grammar(BAR, pitch_range, scale, active)
                    attempts = []
                    candidates = []
                    for attempt in range(4):
                        response = await client.post(
                            model.endpoint,
                            json={
                                "model": model.model,
                                "temperature": 0.89,
                                "seed": 63211
                                + 173 * index
                                + 109 * attempt
                                + 541 * (style == "folk"),
                                "max_tokens": 180,
                                "grammar": grammar,
                                "messages": [
                                    {"role": "system", "content": PHRASE_PROMPT},
                                    {
                                        "role": "user",
                                        "content": json.dumps(context, ensure_ascii=False),
                                    },
                                ],
                            },
                        )
                        selection = response.raise_for_status().json()["choices"][0]
                        raw = selection["message"]["content"]
                        if selection["finish_reason"] != "stop":
                            attempts.append({"raw": raw, "issues": ["Incomplete phrase"]})
                            continue
                        try:
                            draft = decode_phrase(raw, BAR)
                            issues = phrase_issues(
                                draft, active, following, BAR, pitch_range=pitch_range
                            )
                        except ValueError:
                            issues = ["Invalid phrase notation"]
                        attempts.append({"raw": raw, "issues": issues})
                        if not issues:
                            score = preference(draft.notes, prior)
                            candidates.append((score, raw, draft.notes))
                            if all(score[:4]):
                                break
                    if not candidates:
                        raise RuntimeError(
                            f"Qwen did not return playable notes for {style} {label}"
                        )
                    _, raw, notes = max(candidates, key=lambda entry: entry[0])
                    phrases[label] = {
                        "context": context,
                        "attempts": attempts,
                        "selected_raw": raw,
                        "notes": notes,
                    }
                    print(style, label, raw, preference(notes, prior), flush=True)
                    prior = notes
                return {
                    "excerpt": style,
                    "model": model.model,
                    "user_instruction": item["user_instruction"],
                    "backing_chords": chords,
                    "phrases": phrases,
                }
        finally:
            local.close()


async def main(styles):
    source = {item["excerpt"]: item for item in json.loads(BASELINE.read_text())}
    scores = await asyncio.gather(*(compose(style, source[style]) for style in styles))
    filename = "decisions.json" if len(scores) == 2 else f"{styles[0]}-new.json"
    (WORK / filename).write_text(json.dumps(scores, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("styles", nargs="*", help="cinematic, folk, or both when omitted")
    args = parser.parse_args()
    styles = tuple(dict.fromkeys(args.styles)) or tuple(STYLES)
    if any(style not in STYLES for style in styles):
        parser.error("Available styles: cinematic, folk")
    asyncio.run(main(styles))
