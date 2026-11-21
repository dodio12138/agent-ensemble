import asyncio

import pytest
from fastapi.testclient import TestClient

from band.api import create_app
from band.conductor import genre_preset
from band.game_music import GAME_GENRES, game_tones
from band.music import PPQ, TRACK_DEFINITIONS, ConductorPlan, MusicSettings, active_tracks
from band.planner import Planner
from band.style_direction import style_intent

GAME_PARTS = ("strings", "brass", "melody", "chip")
LOOP = ("C", "Am", "F", "G", "C", "Am", "F", "G")


def game(genre, density=0.7, meter="4/4", progression=LOOP):
    conductor, _ = genre_preset(genre, ConductorPlan())
    return MusicSettings(
        lineup="game", conductor=conductor, progression=progression, density=density, meter=meter
    )


def plan(settings, bar):
    return asyncio.run(Planner().plan(bar, settings, 1))


def part(bar_plan, track):
    return sorted(
        (note for note in bar_plan.notes if note.track_id == track),
        key=lambda note: (note.start_tick, note.pitch),
    )


def test_game_lineup_has_its_own_tracks_and_channels():
    assert active_tracks("game") == ("drums", "bass", "piano", *GAME_PARTS)
    channels = [definition.channel for definition in TRACK_DEFINITIONS.values()]
    assert len(channels) == len(set(channels)) and 2 not in channels  # 2 is the live keyboard


@pytest.mark.parametrize("genre", GAME_GENRES)
@pytest.mark.parametrize("meter", ["4/4", "3/4", "6/8"])
def test_every_game_style_plans_valid_bars_without_fallbacks(genre, meter):
    settings = game(genre, meter=meter)
    for bar in range(8):
        result = plan(settings, bar)
        assert not result.fallback_tracks, (genre, meter, bar, result.intents)
        assert all(
            note.start_tick + note.duration_ticks <= settings.bar_ticks for note in result.notes
        )
        for note in result.notes:
            definition = TRACK_DEFINITIONS[note.track_id]
            assert definition.min_pitch <= note.pitch <= definition.max_pitch, note


def test_the_theme_returns_every_phrase_and_cadences_on_the_root():
    settings = game("rpg")
    first, lifted, again = (part(plan(settings, bar), "melody") for bar in (0, 4, 8))
    assert first and [(n.start_tick, n.pitch) for n in first] == [
        (n.start_tick, n.pitch) for n in again
    ]
    # The second phrase repeats the rhythm a little higher, as game loops often do.
    assert [n.start_tick for n in lifted] == [n.start_tick for n in first]
    assert lifted[0].pitch > first[0].pitch
    cadence = part(plan(settings, 3), "melody")
    assert cadence[-1].pitch % 12 == 7  # bar 4 of the loop is G
    # Call and answer differ inside the phrase.
    assert [n.start_tick for n in part(plan(settings, 1), "melody")] != [
        n.start_tick for n in first
    ]


def test_each_style_gives_the_layers_their_game_roles():
    chip = part(plan(game("chiptune", density=0.8), 0), "chip")
    assert len(chip) == 16 and {n.start_tick % (PPQ // 4) for n in chip} == {0}
    assert not part(plan(game("chiptune"), 0), "strings")
    battle = plan(game("battle", density=0.8), 0)
    assert len(part(battle, "strings")) == 16  # sixteenth-note ostinato
    assert {n.pitch for n in part(battle, "drums")} >= {41, 43}  # taiko-like toms
    assert not part(battle, "chip")
    stealth = part(plan(game("stealth"), 0), "strings")
    assert stealth and all(n.duration_ticks <= PPQ // 4 for n in stealth)  # pizzicato
    rpg = [part(plan(game("rpg"), bar), "brass") for bar in range(4)]
    assert rpg[0] and not rpg[1] and not rpg[3]  # a horn call at the phrase start


def test_game_styles_are_recognised_from_words():
    for text, genre in (
        ("来点 8-bit 风格", "chiptune"),
        ("红白机那种", "chiptune"),
        ("横版过关的配乐", "platformer"),
        ("RPG 村庄", "rpg"),
        ("Boss 战", "battle"),
        ("地下城探索", "exploration"),
        ("潜行关卡", "stealth"),
    ):
        assert style_intent(text).genre == genre, text


def test_choosing_a_game_style_on_the_game_lineup_brings_its_sounds(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/sessions", json=MusicSettings(lineup="game").model_dump(mode="json")
        ).json()
        assert tuple(created["tracks"]) == active_tracks("game")
        assert created["tracks"]["piano"]["program"] == 46
        prefix = f"/sessions/{created['id']}"
        client.post(f"{prefix}/preset", json={"style": "chiptune"})
        tracks = client.get(f"{prefix}/state").json()
        staged = (tracks.get("pending") or tracks)["tracks"]
        assert staged["melody"]["program"] == 80 and staged["chip"]["program"] == 80
        assert staged["strings"]["muted"] and staged["brass"]["muted"]
        client.post(f"{prefix}/preset", json={"style": "rpg"})
        state = client.get(f"{prefix}/state").json()
        staged = (state.get("pending") or state)["tracks"]
        assert staged["melody"]["program"] == 73 and not staged["strings"]["muted"]
        assert game_tones("rpg")["piano"]["program"] == 46
