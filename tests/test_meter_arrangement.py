import asyncio
from itertools import product

from band.improv_arrangement import apply_improvised_fill
from band.music import (
    PPQ,
    ConductorPlan,
    GrooveTransition,
    ImprovisationPhrase,
    MusicSettings,
    PlayerPhrase,
    SongSection,
    active_tracks,
    validate_notes,
)
from band.planner import Planner
from band.transition import apply_transition


def test_three_four_and_six_eight_have_different_real_accents():
    async def arrange():
        planner = Planner()
        waltz = MusicSettings(meter="3/4", collaboration="shared", lineup="modern")
        compound = waltz.model_copy(update={"meter": "6/8"})
        for bar_plan in (
            await planner.plan(1, waltz, 1),
            planner.fallback(1, waltz, 1),
        ):
            notes = bar_plan.notes
            assert {note.track_id for note in notes} == set(active_tracks("modern"))
            assert {
                note.start_tick for note in notes if note.pitch == 36 and note.track_id == "drums"
            } == {0}
            assert {
                note.start_tick for note in notes if note.pitch == 37 and note.track_id == "drums"
            } == {
                PPQ,
                2 * PPQ,
            }
            assert {note.start_tick for note in notes if note.track_id == "piano"} == {PPQ, 2 * PPQ}
        for bar_plan in (
            await planner.plan(1, compound, 1),
            planner.fallback(1, compound, 1),
        ):
            notes = bar_plan.notes
            assert {note.track_id for note in notes} == set(active_tracks("modern"))
            assert {
                note.start_tick for note in notes if note.pitch == 37 and note.track_id == "drums"
            } == {3 * PPQ // 2}
            assert {
                note.start_tick for note in notes if note.pitch == 42 and note.track_id == "drums"
            } == set(range(0, 3 * PPQ, PPQ // 2))
            assert {note.start_tick for note in notes if note.track_id == "piano"} == {
                0,
                3 * PPQ // 2,
            }

    asyncio.run(arrange())


def test_band_and_improvised_replies_stay_inside_short_measures():
    async def arrange():
        planner = Planner()
        for meter, style, activity, register in product(
            ("3/4", "6/8"),
            ("swing", "bossa", "funk", "ballad"),
            ("busy", "sparse"),
            ("low", "high"),
        ):
            settings = MusicSettings(
                meter=meter,
                conductor=ConductorPlan(style=style, drums="driving"),
                lineup="modern",
            )
            phrase = PlayerPhrase(activity=activity, register=register, accent_tick=5 * PPQ // 2)
            plan = await planner.plan(1, settings, 1, player_phrase=phrase, voice_leading=True)
            assert plan.player_response.phrase == phrase
            assert not plan.fallback_tracks
            improv = apply_improvised_fill(
                plan, settings, ImprovisationPhrase(lead="piano", character="answer")
            )
            for draft in (plan, improv, planner.fallback(1, settings, 1)):
                assert draft.notes == validate_notes(draft.bar, draft.notes)
                assert (
                    max(note.start_tick + note.duration_ticks for note in draft.notes)
                    <= settings.bar_ticks
                )

    asyncio.run(arrange())


def test_meter_pickups_cue_new_groups_inside_outgoing_measure():
    planner = Planner()
    for outgoing, incoming in (("3/4", "6/8"), ("6/8", "4/4"), ("4/4", "3/4")):
        old, new = MusicSettings(meter=outgoing), MusicSettings(meter=incoming)
        cue = GrooveTransition(
            phase="pickup",
            from_style="swing",
            to_style="swing",
            from_bpm=120,
            to_bpm=120,
            from_meter=outgoing,
            to_meter=incoming,
        )
        pickup = apply_transition(planner.fallback(1, old, 1), old, cue)
        drum_ticks = [
            note.start_tick
            for note in pickup.notes
            if note.track_id == "drums" and note.event_id.startswith("transition:")
        ]
        assert drum_ticks[0] == old.bar_ticks - old.pulse_ticks
        assert len(drum_ticks) == (3 if incoming == "6/8" else 2)
        assert max(note.start_tick + note.duration_ticks for note in pickup.notes) <= old.bar_ticks
        arrival = apply_transition(
            planner.fallback(2, new, 1), new, cue.model_copy(update={"phase": "arrival"})
        )
        assert any(note.start_tick == 0 and note.pitch == 36 for note in arrival.notes)
        assert max(note.start_tick + note.duration_ticks for note in arrival.notes) <= new.bar_ticks


def test_song_motif_repeats_and_answers_over_the_real_chords_in_both_lineups():
    async def arrange():
        planner = Planner()
        section = SongSection(
            role="a",
            bars=4,
            bpm=106,
            meter="6/8",
            progression=("C", "F", "C", "G"),
            motif="rise",
            texture="full",
            intensity=65,
        )
        for lineup, lead in (("modern", "lead_guitar"), ("trio", "piano")):
            settings = MusicSettings(
                meter=section.meter,
                bpm=section.bpm,
                progression=section.progression * 2,
                lineup=lineup,
            )
            motifs = []
            for offset in range(4):
                draft = await planner.plan(offset, settings, 1, section=section, section_bar=offset)
                fallback = planner.fallback(
                    offset, settings, 1, section=section, section_bar=offset
                )
                for plan in (draft, fallback):
                    assert plan.notes == validate_notes(plan.bar, plan.notes)
                    assert any(
                        note.track_id == lead
                        and note.event_id.endswith("-motif")
                        and note.start_tick == (0 if offset < 3 else PPQ // 2)
                        for note in plan.notes
                    )
                notes = [
                    note
                    for note in draft.notes
                    if note.track_id == lead and note.event_id.endswith("-motif")
                ]
                harmony = settings.chord_at(offset)
                pitches = {(harmony.root + interval) % 12 for interval in harmony.intervals}
                assert all(note.pitch % 12 in pitches for note in notes)
                motifs.append(tuple((note.start_tick, note.pitch) for note in notes))
            assert motifs[0] == motifs[2]
            assert motifs[0] != motifs[1]
            assert motifs[-1][-1][1] % 12 == settings.chord_at(3).root

            playing = PlayerPhrase(activity="busy", register="high", accent_tick=PPQ // 2)
            listening = await planner.plan(0, settings, 1, section=section, player_phrase=playing)
            assert all(
                note.track_id != lead or not note.event_id.endswith("-motif")
                for note in listening.notes
            )

    asyncio.run(arrange())


def test_all_section_motifs_on_compound_and_simple_grids_have_positive_gates():
    planner = Planner()
    for meter, texture, contour, lineup in product(
        ("4/4", "3/4", "6/8"),
        ("sparse", "full", "answer"),
        ("rise", "fall", "arch", "repeat"),
        ("trio", "modern"),
    ):
        settings = MusicSettings(meter=meter, lineup=lineup)
        section = SongSection(
            role="b",
            bars=4,
            bpm=settings.bpm,
            meter=meter,
            progression=settings.progression[:4],
            motif=contour,
            texture=texture,
            intensity=65,
        )
        previous = None
        for offset in range(section.bars):
            plan = planner.fallback(
                offset, settings, 1, section=section, section_bar=offset, prior_chord=previous
            )
            melody = [note for note in plan.notes if note.event_id.endswith("-motif")]
            assert len(melody) >= 2
            assert len({note.start_tick for note in melody}) == len(melody)
            assert all(
                note.duration_ticks > 0
                and note.start_tick + note.duration_ticks <= settings.bar_ticks
                for note in melody
            )
            previous = settings.chord_at(offset)
