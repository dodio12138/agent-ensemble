import asyncio
import itertools

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
    # The verse chugs: eight palm-muted downstrokes, low string first, with open accents.
    for strum in (rhythm[:3], rhythm[3:6]):
        assert [note.pitch for note in strum] == sorted(note.pitch for note in strum)
    assert any(note.articulation == "palm_mute" for note in rhythm)
    assert rhythm[0].articulation == "normal"
    # The lift rings out, and its offbeat pushes are upstrokes, high string first.
    lift = [note for note in ending.notes if note.track_id == "rhythm_guitar"]
    push = [note for note in lift if 3 * PPQ // 2 <= note.start_tick < 2 * PPQ]
    assert not any(note.articulation == "palm_mute" for note in lift)
    assert [note.pitch for note in push] == sorted((note.pitch for note in push), reverse=True)
    assert not any(note.track_id == "lead_guitar" for note in answer.notes)
    lick = sorted(
        (note for note in fill.notes if note.track_id == "lead_guitar"),
        key=lambda note: note.start_tick,
    )
    assert len(lick) >= 4 and any(note.articulation == "legato" for note in lick)
    # The line sings: each note runs into the next, and the last one rings out.
    for note, following in itertools.pairwise(lick):
        assert note.start_tick + note.duration_ticks >= following.start_tick
    assert lick[-1].duration_ticks >= PPQ
    assert lick[-1].start_tick + lick[-1].duration_ticks <= 4 * PPQ
    assert max(note.pitch for note in lick) <= 81  # not up in the thin, piercing range
    climax = [note for note in ending.notes if note.track_id == "lead_guitar"]
    assert climax and not any(note.articulation == "harmonic" for note in climax)
    assert any(
        note.track_id == "drums" and note.pitch == 45 and note.start_tick >= 3 * PPQ
        for note in fill.notes
    )
