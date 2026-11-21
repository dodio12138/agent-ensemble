import pytest
from pydantic import ValidationError

from band.music import MusicSettings
from band.phrase_generation import decode_phrase
from band.phrase_score import PhraseDraft, phrase_issues
from band.planner import BarContext, LeadGuitarAgent
from band.song import build_song


def test_phrase_rows_are_exactly_four_integers():
    with pytest.raises(ValidationError):
        PhraseDraft.model_validate({"notes": [[0, 12, 72, "84"]]}, strict=True)
    with pytest.raises(ValidationError):
        PhraseDraft.model_validate({"notes": [[0, 12, 72]]}, strict=True)
    schema = PhraseDraft.output_schema()
    assert schema["required"] == ["notes"]
    assert schema["properties"]["notes"]["items"]["maxItems"] == 4


def test_phrase_reviewer_records_repairs_for_bad_timing_range_and_overlap():
    draft = PhraseDraft(notes=[[0, 18, 67, 77], [12, 12, 71, 70], [180, 20, 108, 0]])
    issues = phrase_issues(draft, ["C", "Em"], "C", 96)
    assert any("monophonic" in issue for issue in issues)
    assert any("steps 0–191" in issue for issue in issues)
    assert any("MIDI pitch" in issue for issue in issues)
    assert any("velocity" in issue for issue in issues)


def test_actual_cadence_chord_is_used_even_when_the_phrase_ends_early():
    resolves_in_first_bar = PhraseDraft(notes=[[0, 18, 60, 77]])
    assert phrase_issues(resolves_in_first_bar, ["C", "Ab7"], None, 96) == []

    unresolved = PhraseDraft(notes=[[0, 18, 60, 77], [120, 18, 62, 77]])
    assert "Ab7" in phrase_issues(unresolved, ["C", "Ab7"], None, 96)[0]


def test_phrase_can_tie_a_shared_cadence_pitch_and_can_opt_in_to_anticipation():
    # G can ring across C into G, whereas E is no longer a chord tone over G.
    shared = PhraseDraft(notes=[[84, 20, 67, 81]])
    assert phrase_issues(shared, ["C", "G"], None, 96, allow_cross_bar=True) == []
    assert "bar line" in phrase_issues(shared, ["C", "G"], None, 96)[0]

    clashing = PhraseDraft(notes=[[84, 20, 64, 81]])
    assert "C and G" in phrase_issues(clashing, ["C", "G"], None, 96, allow_cross_bar=True)[0]

    anticipated = PhraseDraft(notes=[[188, 4, 72, 70]])
    assert phrase_issues(anticipated, ["C", "G"], "C", 96, allow_anticipation=True) == []
    assert phrase_issues(anticipated, ["C", "G"], "C", 96)


def test_relative_notation_preserves_rests_and_cannot_spill_into_the_answer():
    draft = decode_phrase("8/2/72/64 6/14/76/93|0/2/74/85 1/14/71/77", 96)
    assert draft.notes == [
        [24, 6, 72, 64],
        [48, 42, 76, 93],
        [96, 6, 74, 85],
        [105, 42, 71, 77],
    ]
    with pytest.raises(ValueError, match="bar line"):
        decode_phrase("20/20/72/64|1/14/71/77", 96)


def test_guitar_plays_the_score_verbatim_and_uses_rules_for_unscored_bars():
    settings = MusicSettings(lineup="modern")
    song, _ = build_song(settings, "C 大调，流行摇滚", None)
    intro = song.sections[0]
    lead = ((6, 15, 72, 64), (25, 30, 76, 91))
    section = type(intro).model_validate({**intro.model_dump(), "lead": (lead, None, (), None)})
    agent = LeadGuitarAgent()
    chord = settings.chord_at(0)
    played = agent.arrange(BarContext(0, settings, chord, section=section)).notes
    assert [(n.start_tick, n.duration_ticks, n.pitch) for n in played] == [
        (240, 600, 72),
        (1000, 1200, 76),
    ]
    assert played[1].velocity > played[0].velocity
    fallback = BarContext(1, settings, chord, section=section, section_bar=1)
    assert (
        agent.arrange(fallback).notes
        == agent.arrange(BarContext(1, settings, chord, section=intro, section_bar=1)).notes
    )
    assert not agent.arrange(BarContext(2, settings, chord, section=section, section_bar=2)).notes
