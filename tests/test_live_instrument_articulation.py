import asyncio

from band.conductor import Conductor
from band.music import PPQ, MusicSettings, validate_notes
from band.planner import Planner


def test_rock_guitar_strums_in_real_string_order_and_keeps_distinct_articulations():
    async def generate():
        conductor = Conductor()
        base = MusicSettings(lineup="modern", density=0.75)
        rock = await conductor.propose("硬摇滚", base.conductor)
        settings = base.model_copy(update={"conductor": rock.plan, "progression": rock.progression})
        return [await Planner().plan(bar, settings, 0) for bar in (0, 1, 3, 7)]

    first, answer, fill, ending = asyncio.run(generate())
    for bar in (first, answer, fill, ending):
        assert validate_notes(bar.bar, bar.notes) == bar.notes
    rhythm = [note for note in first.notes if note.track_id == "rhythm_guitar"]
    assert len(rhythm) >= 9 and len({note.start_tick for note in rhythm}) == len(rhythm)
    assert [note.pitch for note in rhythm[:3]] == sorted(note.pitch for note in rhythm[:3])
    assert [note.pitch for note in rhythm[3:6]] == sorted(
        (note.pitch for note in rhythm[3:6]), reverse=True
    )
    assert any(note.articulation == "palm_mute" for note in rhythm)
    lead = [note for note in answer.notes if note.track_id == "lead_guitar"]
    assert lead[1].articulation == "legato"
    assert lead[0].start_tick + lead[0].duration_ticks > lead[1].start_tick
    assert any(note.articulation == "harmonic" for note in ending.notes)
    assert any(
        note.track_id == "drums" and note.pitch == 45 and note.start_tick >= 3 * PPQ
        for note in fill.notes
    )
