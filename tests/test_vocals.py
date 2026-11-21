import asyncio
import itertools
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_midi_output import FakePort

from band.api import create_app
from band.conductor import Conductor, genre_preset, rule_plan, vocal_request
from band.midi_output import MidiPortOutput
from band.music import PPQ, ConductorPlan, MusicSettings, active_tracks, validate_notes
from band.planner import Planner
from band.style_arrangement import ArrangementChoice, materialize
from band.vocals import VOCAL_GUIDE, vocal_options, vocal_role

LOOP = ("C", "Am", "F", "G", "C", "Am", "F", "G")


def styled(genre, lineup="trio", **changes):
    conductor, _ = genre_preset(genre, ConductorPlan())
    return MusicSettings(lineup=lineup, conductor=conductor, progression=LOOP, **changes)


def voice(settings, bar):
    plan = asyncio.run(Planner().plan(bar, settings, 1))
    assert not plan.fallback_tracks
    return sorted(
        (note for note in plan.notes if note.track_id == "vocal"),
        key=lambda note: (note.start_tick, note.pitch),
    )


def test_every_lineup_has_a_voice_with_a_style_role():
    assert all(active_tracks(lineup)[-1] == "vocal" for lineup in ("trio", "modern", "game"))
    assert vocal_role(styled("swing")) == "scat"
    assert vocal_role(styled("pop", "modern")) == "lead"
    assert vocal_role(styled("cinematic")) == "pad"
    assert vocal_role(styled("chiptune", "game")) == "off"
    # On the game lineup the theme instrument carries the tune, so the voice never leads.
    assert vocal_role(styled("rpg", "game")) == "pad"
    assert "lead" not in vocal_options("rpg", "game") and "lead" in vocal_options("rpg", "trio")


@pytest.mark.parametrize("genre", ["swing", "pop", "funk", "cinematic", "battle", "stealth"])
@pytest.mark.parametrize("lineup", ["trio", "modern", "game"])
def test_the_voice_plans_valid_bars_in_every_lineup(genre, lineup):
    settings = styled(genre, lineup, vocal_glide_amount=1.0)
    for bar in range(8):
        notes = tuple(voice(settings, bar))
        assert validate_notes(bar, notes) == notes


def test_roles_sing_differently():
    pad = voice(styled("cinematic"), 0)
    assert len(pad) == 3 and all(n.start_tick == 0 and n.duration_ticks == 4 * PPQ for n in pad)
    backing = [voice(styled("funk", "modern"), bar) for bar in range(2)]
    assert not backing[0] and len(backing[1]) == 2 and backing[1][0].start_tick == 2 * PPQ
    assert not voice(styled("chiptune", "game"), 0)
    # A lead singer takes one four-bar phrase, then steps back to backing.
    lead = styled("pop", vocal_glide=False)
    assert len(voice(lead, 0)) >= 3 and len(voice(lead, 5)) == 2
    # Scat sits on swung eighths, like the rest of a swing band.
    swung = {note.start_tick % PPQ for bar in range(4) for note in voice(styled("swing"), bar)}
    assert swung <= {0, PPQ * 2 // 3, PPQ // 4, PPQ * 3 // 4}


def test_slides_and_runs_follow_the_switch_and_strength():
    plain = [note for bar in range(4) for note in voice(styled("pop", vocal_glide=False), bar)]
    assert plain and all(note.slide_from is None for note in plain)
    strong = [
        note for bar in range(4) for note in voice(styled("pop", vocal_glide_amount=1.0), bar)
    ]
    slid = [note for note in strong if note.slide_from is not None]
    assert len(slid) >= len(strong) // 2
    assert any(note.duration_ticks == PPQ // 8 for note in strong)  # quick runs (转音)
    # Tied notes leave no gap: the voice keeps sounding from one pitch to the next.
    for bar in range(4):
        notes = voice(styled("pop", vocal_glide_amount=1.0), bar)
        for left, right in itertools.pairwise(notes):
            if right.slide_from == left.pitch:
                assert left.start_tick + left.duration_ticks == right.start_tick
    light = [note for bar in range(4) for note in voice(styled("pop", vocal_glide_amount=0.2), bar)]
    assert max(n.slide_ms or 0 for n in light) < max(n.slide_ms or 0 for n in slid)
    assert not any(note.duration_ticks <= PPQ // 6 for note in light)  # no runs when subtle


def test_words_ask_for_a_way_of_singing():
    assert vocal_request("加一段合唱") == "pad"
    assert vocal_request("人声领唱旋律") == "lead"
    assert vocal_request("来点伴唱") == "backing"
    assert vocal_request("用拟声唱") == "scat"
    assert vocal_request("不要人声，纯器乐") == "off"
    assert vocal_request("和声进行改成 1-5-6-4") is None
    current = ConductorPlan(style="ballad", vocal="pad")
    assert rule_plan("强度 70%", current).vocal == "pad"  # unrelated changes keep the voice
    assert rule_plan("改成放克", current).vocal is None  # a new style brings its own
    assert rule_plan("人声改成伴唱", current).vocal == "backing"
    result = asyncio.run(Conductor().propose("不要人声", current))
    assert result.plan.vocal == "off"


def test_the_arranger_is_taught_the_voice_and_kept_in_bounds():
    assert "vocal_options" in VOCAL_GUIDE and "scat" in VOCAL_GUIDE
    choice = ArrangementChoice(
        groove="straight_eighth",
        harmony="keep",
        mode="minor",
        tonic="C",
        bpm=80,
        intensity=40,
        density=0.4,
        drums="sparse",
        bass="root",
        keys="sustained",
        rhythm_guitar="clean",
        lead_guitar="clean",
        keys_tone="piano",
        pad="strings",
        vocal="scat",
        note="电影感长音铺底，合唱托住和声",
    )
    arranged = materialize("cinematic", choice, styled("cinematic"))
    assert arranged.plan.vocal == "pad" and arranged.tracks["vocal"]["program"] == 52
    assert any("人声" in correction for correction in arranged.corrections)
    kept = materialize("cinematic", choice.model_copy(update={"vocal": "off"}), styled("cinematic"))
    assert kept.plan.vocal == "off" and not kept.corrections


def test_slides_reach_midi_as_portamento():
    output = MidiPortOutput(f"test-{uuid4().hex[:6]}", factory=FakePort)
    port = output.port
    note = {"type": "note_on", "track_id": "vocal", "pitch": 69, "velocity": 80, "volume": 0.6}
    output.deliver({**note, "slide_from": 67, "slide_ms": 120})
    assert port.sent[:4] == [(0xBB, 65, 127), (0xBB, 5, 0), (0xBB, 37, 120), (0xBB, 84, 67)]
    output.deliver({**note, "pitch": 71})
    assert (0xBB, 65, 0) in port.sent
    output.close()


def test_the_glide_switch_and_strength_are_settings(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        state = client.post("/sessions").json()
        assert state["settings"]["vocal_glide"] and state["settings"]["vocal_glide_amount"] == 0.5
        prefix = f"/sessions/{state['id']}"
        patched = client.patch(
            f"{prefix}/settings", json={"vocal_glide": False, "vocal_glide_amount": 0.8}
        ).json()
        desired = (patched.get("pending") or patched)["settings"]
        assert desired["vocal_glide"] is False and desired["vocal_glide_amount"] == 0.8
        bad = client.patch(f"{prefix}/settings", json={"vocal_glide_amount": 2})
        assert bad.status_code == 422
