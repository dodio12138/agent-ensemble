"""Compare three approaches with identical chord, groove, density, and seed."""

import asyncio
import json

from band.music import PPQ, ConductorPlan, MusicSettings
from band.planner import Planner


async def compare() -> list[dict]:
    planner = Planner()
    results = []
    for mode in ("solo", "shared", "negotiate"):
        settings = MusicSettings(
            collaboration=mode,
            density=0.9,
            conductor=ConductorPlan(style="funk"),
        )
        result = {
            "mode": mode,
            "kick_onsets": 0,
            "kick_onsets_aligned_with_bass": 0,
            "piano_onsets_near_bass": 0,
            "proposals": 0,
            "agreements": 0,
        }
        for bar in range(len(settings.progression)):
            plan = await planner.plan(bar, settings, 0)
            kick = {
                note.start_tick
                for note in plan.notes
                if note.track_id == "drums" and note.pitch == 36
            }
            bass = {note.start_tick for note in plan.notes if note.track_id == "bass"}
            piano = {note.start_tick for note in plan.notes if note.track_id == "piano"}
            result["kick_onsets"] += len(kick)
            result["kick_onsets_aligned_with_bass"] += len(kick & bass)
            result["piano_onsets_near_bass"] += sum(
                any(abs(tick - low) <= PPQ // 2 for low in bass) for tick in piano
            )
            result["proposals"] += sum(event.stage == "proposal" for event in plan.negotiation)
            result["agreements"] += sum(event.stage == "accepted" for event in plan.negotiation)
        results.append(result)
    return results


if __name__ == "__main__":
    print(json.dumps(asyncio.run(compare()), indent=2))
