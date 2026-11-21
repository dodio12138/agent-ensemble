import asyncio

from band.improv_arrangement import apply_improvised_fill, phrase_for_bar, project_improvisation
from band.music import (
    CHORUS_BARS,
    PPQ,
    TRACKS,
    ConductorPlan,
    ImprovisationPhrase,
    ImprovisationPlan,
    MusicSettings,
    PlayerPhrase,
    validate_notes,
)
from band.planner import Planner

PLAN = ImprovisationPlan(
    progression=("Dm9", "G13", "Cmaj7", "Am9", "Dm7", "G7b9", "Em7", "A7"),
    phrases=(
        ImprovisationPhrase(lead="piano", character="space"),
        ImprovisationPhrase(lead="bass", character="answer"),
        ImprovisationPhrase(lead="drums", character="build"),
        ImprovisationPhrase(lead="piano", character="resolve"),
    ),
)


def test_projection_changes_lead_density_and_harmony_only_within_chorus():
    base = MusicSettings(bpm=103, conductor=ConductorPlan(style="bossa"))
    start = 3
    phrases = [phrase_for_bar(PLAN, bar, start) for bar in range(start, start + CHORUS_BARS)]
    assert phrases == [phrase for item in PLAN.phrases for phrase in (item, item)]
    assert project_improvisation(base, start - 1, start, PLAN, 1) is base
    assert project_improvisation(base, start + CHORUS_BARS, start, PLAN, 1) is base
    builds = [
        project_improvisation(base, bar, start, PLAN, 1)
        for bar in range(start, start + CHORUS_BARS, 2)
    ]
    space, answer, build, resolve = builds
    assert space.conductor.drums == "sparse"
    assert answer.conductor.bass == "syncopated"
    assert build.conductor.drums == "driving"
    assert resolve.conductor.piano == "sustained"
    assert space.density < answer.density < build.density
    assert resolve.density < answer.density
    assert all(s.conductor.style == "bossa" and s.bpm == 103 for s in builds)
    assert space.chord_at(start).symbol == "Am9"
    assert base.chord_at(start).symbol == "Am7"
    assert build.seed != project_improvisation(base, start + 4, start, PLAN, 2).seed


def test_lead_fills_sound_within_valid_score_and_respect_busy_player():
    async def verify():
        planner = Planner()
        for style in ("swing", "bossa", "funk", "ballad"):
            settings = MusicSettings(conductor=ConductorPlan(style=style))
            reply_count = 0
            for bar in range(CHORUS_BARS):
                phrase = phrase_for_bar(PLAN, bar, 0)
                projected = project_improvisation(settings, bar, 0, PLAN, 1)
                baseline = await planner.plan(bar, projected, 1)
                reply = apply_improvised_fill(baseline, projected, phrase)
                assert reply.intents == baseline.intents
                assert reply.negotiation == baseline.negotiation
                assert validate_notes(bar, reply.notes) == reply.notes
                assert {note.track_id for note in reply.notes} - {"vocal"} == TRACKS.keys()
                chord = projected.chord_at(bar)
                # Solos may pass through any note, but what lands on a beat or is held
                # must be a chord tone or a 9th / 13th colour.
                allowed = {(chord.root + degree) % 12 for degree in (*chord.intervals, 2, 9)}
                for note in reply.notes:
                    if note.event_id.startswith("improv-"):
                        reply_count += 1
                        assert note.track_id == phrase.lead
                        on_beat = note.start_tick % PPQ == 0
                        held = note.duration_ticks >= PPQ // 2
                        if note.track_id != "drums" and (on_beat or held):
                            assert note.pitch % 12 in allowed, (style, bar, note)
                busy = await planner.plan(
                    bar,
                    projected,
                    1,
                    player_phrase=PlayerPhrase(pitch_register="high", activity="busy"),
                )
                assert apply_improvised_fill(busy, projected, phrase) is busy
            assert reply_count

    asyncio.run(verify())
