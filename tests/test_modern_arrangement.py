"""Modern lineup figures, graceful fallback, and keyboard interplay."""

import asyncio

from band.music import PPQ, TRACK_DEFINITIONS, MusicSettings, PlayerPhrase
from band.planner import Planner

MODERN_PARTS = {"rhythm_guitar", "lead_guitar", "synth"}


def test_modern_lineup_has_independent_playable_parts_in_both_planners():
    async def verify():
        planner = Planner()
        settings = MusicSettings(lineup="modern", progression=("C", "F") * 4)
        trio = await planner.plan(1, MusicSettings(), 1)
        assert MODERN_PARTS.isdisjoint(decision.track_id for decision in trio.intents)

        for plan in (await planner.plan(1, settings, 1), planner.fallback(1, settings, 1)):
            parts = {
                track: [n for n in plan.notes if n.track_id == track] for track in MODERN_PARTS
            }
            assert all(parts.values())
            for track, notes in parts.items():
                definition = TRACK_DEFINITIONS[track]
                assert all(note.channel == definition.channel for note in notes)
                assert {note.pitch % 12 for note in notes} <= {0, 5, 9}
            assert all(note.duration_ticks < PPQ for note in parts["rhythm_guitar"])
            assert all(note.start_tick >= 2 * PPQ for note in parts["lead_guitar"])
            assert all(note.duration_ticks >= 2 * PPQ for note in parts["synth"])

    asyncio.run(verify())


def test_modern_lead_and_pad_leave_room_for_a_busy_keyboard():
    async def verify():
        planner = Planner()
        settings = MusicSettings(lineup="modern")
        player = PlayerPhrase(register="high", activity="busy", accent_tick=PPQ)
        plan = await planner.plan(1, settings, 1, player_phrase=player)
        assert plan.player_response.phrase == player
        assert not plan.fallback_tracks
        assert any(note.track_id == "rhythm_guitar" for note in plan.notes)
        assert {"lead_guitar", "synth"}.isdisjoint(note.track_id for note in plan.notes)

    asyncio.run(verify())
