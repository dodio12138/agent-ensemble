import asyncio
import itertools

import pytest

from band.conductor import genre_preset
from band.expression import FLAVORS, GLIDE_TRACKS, express
from band.music import PPQ, ConductorPlan, Glide, Glides, MusicSettings, Note
from band.planner import Planner

LOOP = ("Am", "G", "F", "E7", "Am", "G", "F", "E7")


def band(genre, lineup, track=None, amount=1.0, density=0.75):
    conductor, _ = genre_preset(genre, ConductorPlan())
    glides = Glides(**({track: Glide(on=True, amount=amount)} if track else {}))
    return MusicSettings(
        lineup=lineup, conductor=conductor, progression=LOOP, glide=glides, density=density
    )


def line(settings, track, bars=range(8)):
    notes = []
    for bar in bars:
        plan = asyncio.run(Planner().plan(bar, settings, 1))
        assert not plan.fallback_tracks, plan.intents
        notes.extend(
            sorted((n for n in plan.notes if n.track_id == track), key=lambda n: n.start_tick)
        )
    return notes


def test_every_melodic_track_has_a_flavor_and_only_the_voice_slides_by_default():
    assert set(GLIDE_TRACKS) == {
        "vocal",
        "lead_guitar",
        "melody",
        "chip",
        "bass",
        "strings",
        "brass",
    }
    assert all(FLAVORS[track].label for track in GLIDE_TRACKS)
    settings = band("hard_rock", "modern")
    for track in ("lead_guitar", "bass"):
        assert all(note.slide_from is None for note in line(settings, track))


def test_lead_guitar_bends_up_and_hammers_on_from_below():
    notes = line(band("hard_rock", "modern", "lead_guitar"), "lead_guitar")
    slid = [n for n in notes if n.slide_from is not None]
    assert slid and all(n.slide_ms <= 200 for n in slid)
    bends = [n for n in slid if n.slide_from < n.pitch]
    assert bends
    runs = [n for n in notes if "-run" in n.event_id]
    if runs:  # hammer-ons come from below the target note
        first = runs[0]
        target = next(
            n for n in notes if n.start_tick > first.start_tick and "-run" not in n.event_id
        )
        assert first.pitch < target.pitch


def test_bass_slides_into_notes_without_tying_the_groove():
    plain = line(band("funk", "modern"), "bass")
    shaped = line(band("funk", "modern", "bass"), "bass")
    assert [n.duration_ticks for n in plain] == [n.duration_ticks for n in shaped]
    assert any(n.slide_from is not None and n.pitch - n.slide_from >= 2 for n in shaped)


def test_chip_arpeggio_glides_like_a_portamento_square_wave():
    notes = line(band("chiptune", "game", "chip"), "chip", bars=range(2))
    assert all(
        a.start_tick + a.duration_ticks == b.start_tick
        for a, b in itertools.pairwise(notes)
        if b.start_tick
    )
    assert sum(n.slide_from is not None for n in notes) >= len(notes) - 4
    assert all(n.slide_from is None or abs(n.slide_from - n.pitch) <= 12 for n in notes)
    assert not any("-run" in n.event_id for n in notes)


def test_chords_are_left_alone_and_single_lines_are_shaped():
    pad = line(band("cinematic", "game", "strings"), "strings", bars=range(2))
    assert pad and all(n.slide_from is None for n in pad)  # sustained chords
    ostinato = line(band("battle", "game", "strings", density=0.5), "strings", bars=range(2))
    assert any(n.slide_from is not None for n in ostinato)


@pytest.mark.parametrize("track", GLIDE_TRACKS)
def test_strength_scales_the_glide(track):
    settings = band("pop", "modern", track, amount=0.2)
    strong = band("pop", "modern", track, amount=1.0)
    channel = {
        "vocal": 11,
        "lead_guitar": 4,
        "melody": 8,
        "chip": 10,
        "bass": 1,
        "strings": 6,
        "brass": 7,
    }[track]
    pitches = [64, 67, 69, 72] if track != "bass" else [40, 43, 45, 48]
    notes = tuple(
        Note(
            event_id=f"{track}-0-{i * PPQ}-{pitch}",
            track_id=track,
            bar=0,
            start_tick=i * PPQ,
            duration_ticks=PPQ - 40,
            pitch=pitch,
            velocity=80,
            channel=channel,
        )
        for i, pitch in enumerate(pitches)
    )
    light, heavy = express(track, settings, 0, notes), express(track, strong, 0, notes)
    light_ms = [n.slide_ms for n in light if n.slide_ms and n.slide_from in pitches]
    heavy_ms = [n.slide_ms for n in heavy if n.slide_ms and n.slide_from in pitches]
    if FLAVORS[track].tie or track in {"bass", "brass"}:
        assert heavy_ms and (not light_ms or max(light_ms) < max(heavy_ms))
    off = MusicSettings(glide=Glides(**{track: Glide(on=False, amount=1.0)}))
    assert express(track, off, 0, notes) == notes
