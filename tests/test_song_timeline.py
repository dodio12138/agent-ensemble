from io import BytesIO

from fastapi.testclient import TestClient
from mido import MidiFile, bpm2tempo
from test_engine import Clock

from band.api import create_app
from band.music import MusicSettings
from band.planner import Planner
from band.song import SongChoice, build_song
from band.timing import Scheduler

PROMPT = "流行摇滚器乐曲，E 大调，主歌 4/4，副歌转成 6/8 和 132 BPM，尾声回 4/4"


def test_section_timing_is_explicit_or_inherited():
    theme = MusicSettings(bpm=108, meter="6/8")
    song, _ = build_song(theme, PROMPT, None)
    assert song.tonic == "E"
    assert [(s.role, s.bpm, s.meter) for s in song.sections] == [
        ("intro", 108, "4/4"),
        ("a", 108, "4/4"),
        ("b", 132, "6/8"),
        ("a", 108, "4/4"),
        ("outro", 108, "4/4"),
    ]
    steady, _ = build_song(theme, "写一首雷鬼器乐，尾声留白", None)
    assert {(s.bpm, s.meter) for s in steady.sections} == {(108, "6/8")}


def test_model_timing_is_applied_only_to_the_requested_axis():
    choice = SongChoice(
        form="compact",
        genre="rock",
        groove="straight_eighth",
        body_bars=4,
        a_harmony="major_145",
        b_harmony="pop_1564",
        a_motif="arch",
        b_motif="rise",
        b_texture="full",
        keys_tone="electric",
        mix="balanced",
        bpm=102,
        meter="3/4",
        contrast_bpm=128,
        contrast_meter="6/8",
    )
    current = MusicSettings()
    song, _ = build_song(current, "流行摇滚，速度按需调整，拍号保持不变，旋律突出", choice)
    assert {s.meter for s in song.sections} == {"4/4"}
    assert song.sections[2].bpm == 128
    assert song.sections[2].mix == "lead"
    assert song.sections[1].bpm == 102


def test_live_meter_and_bpm_apply_together_at_the_next_chorus():
    clock = Clock()
    scheduler = Scheduler(Planner(), clock=clock, threaded=False)
    try:
        scheduler.play()
        state = scheduler.stage_settings({"meter": "6/8", "bpm": 90}, next_chorus=True)
        assert state["pending"]["bar"] == 8 and state["pending"]["pickup_bar"] == 7
        assert state["meter"] == {"numerator": 4, "denominator": 4}
        while state["current_bar"] < 9:
            state = clock.step(scheduler)
        bars = [e for e in scheduler.events_after(0) if e["type"] == "bar"]
        assert [e["time_signature"] for e in bars[7:10]] == ["4/4", "6/8", "6/8"]
        assert bars[8]["due_ns"] - bars[7]["due_ns"] == 2_000_000_000
        assert bars[9]["due_ns"] - bars[8]["due_ns"] == 2_000_000_000
        assert state["bar_ticks"] == 2880 and state["pulse_ticks"] == 1440
    finally:
        scheduler.close()


def test_song_export_carries_midi_tempo_and_meter_at_section_ticks(tmp_path):
    song, driver = build_song(MusicSettings(bpm=108), PROMPT, None)
    body = MusicSettings(lineup="modern", song=song, conductor=driver).model_dump(mode="json")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        state = client.post("/sessions", json=body).raise_for_status().json()
        path = f"/sessions/{state['id']}"
        assert state["section"]["role"] == "intro"
        midi = MidiFile(file=BytesIO(client.get(f"{path}/export.mid").raise_for_status().content))
        pos = 0
        changes = []
        for event in midi.tracks[0]:
            pos += event.time
            if event.type in {"set_tempo", "time_signature"}:
                changes.append(
                    (pos, event.type, getattr(event, "tempo", getattr(event, "numerator", 0)))
                )
        start_b = (song.sections[0].bars + song.sections[1].bars) * 3840
        assert (start_b, "time_signature", 6) in changes
        assert (start_b, "set_tempo", bpm2tempo(132)) in changes
        assert pos == sum(s.bars * (2880 if s.meter != "4/4" else 3840) for s in song.sections)
        assert len(midi.tracks) == 7
        assert sum(event.type == "note_on" for event in midi.tracks[5]) > 20
        assert client.delete(f"{path}/composition").raise_for_status().json()["section"] is None


def test_one_shot_song_runs_through_outro_without_resetting_between_sections():
    clock = Clock()
    base = MusicSettings(lineup="modern", loop=False, bpm=180)
    song, driver = build_song(base, PROMPT, None)
    settings = base.model_copy(update={"song": song, "conductor": driver})
    scheduler = Scheduler(Planner(), settings, clock=clock, threaded=False, history_size=12000)
    try:
        state = scheduler.play()
        while state["transport"] == "playing":
            state = clock.step(scheduler)
        timeline = [event for event in scheduler.events_after(0) if event["type"] == "bar"]
        assert len(timeline) == song.total_bars
        assert [event["bar"] for event in timeline] == list(range(song.total_bars))
        assert timeline[-1]["time_signature"] == "4/4"
        assert any(event["time_signature"] == "6/8" for event in timeline)
    finally:
        scheduler.close()
