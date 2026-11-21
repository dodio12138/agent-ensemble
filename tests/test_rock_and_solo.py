import asyncio
import os
import subprocess
import sys
import time

import pytest

from band.conductor import genre_preset
from band.improv_arrangement import apply_improvised_fill
from band.music import (
    PPQ,
    ConductorPlan,
    ImprovisationPhrase,
    MusicSettings,
    validate_notes,
)
from band.planner import Planner
from band.process_watchdog import release, watch

MINOR_ROCK = ("Am", "G", "F", "G", "Am", "G", "F", "E7")


def rock(density=0.75, progression=MINOR_ROCK):
    plan, _ = genre_preset("hard_rock", ConductorPlan())
    return MusicSettings(
        lineup="modern", conductor=plan, progression=progression, density=density, bpm=132
    )


def plan(settings, bar):
    return asyncio.run(Planner().plan(bar, settings, 1))


def part(bar_plan, track):
    return sorted(
        (note for note in bar_plan.notes if note.track_id == track),
        key=lambda note: (note.start_tick, note.pitch),
    )


def test_rock_keys_hold_triads_above_the_guitar_instead_of_doubling_it():
    verse = plan(rock(), 0)
    keys, guitar = part(verse, "piano"), part(verse, "rhythm_guitar")
    assert {note.start_tick for note in keys} == {0, 2 * PPQ}
    assert all(note.duration_ticks >= PPQ for note in keys)
    assert {note.pitch % 12 for note in keys} == {9, 0, 4}  # A minor triad, not a bare fifth
    mean = lambda notes: sum(note.pitch for note in notes) / len(notes)
    assert mean(keys) > mean(guitar)
    assert len({note.start_tick // (PPQ // 2) for note in guitar}) == 8  # driving eighths


def test_rock_drums_crash_into_the_cycle_and_ride_through_the_lift():
    first, verse, lift = plan(rock(), 0), plan(rock(), 1), plan(rock(), 5)
    assert any(note.pitch == 49 and note.start_tick == 0 for note in part(first, "drums"))
    assert any(note.pitch == 42 for note in part(verse, "drums"))
    lift_drums = part(lift, "drums")
    assert any(note.pitch == 51 for note in lift_drums)
    assert not any(note.pitch == 42 for note in lift_drums)
    # Below the lift density the band stays in the verse texture.
    assert any(note.pitch == 42 for note in part(plan(rock(density=0.6), 5), "drums"))


def test_rock_lead_licks_follow_the_key_of_the_chords_played():
    for progression, tonic in ((MINOR_ROCK, 9), (("E", "D", "A", "E") * 2, 4)):
        lick = part(plan(rock(progression=progression), 3), "lead_guitar")
        pentatonic = {(tonic + step) % 12 for step in (0, 3, 5, 7, 10)}
        assert len(lick) >= 5 and {note.pitch % 12 for note in lick} <= pentatonic
    assert not part(plan(rock(), 1), "lead_guitar")


def improvise(settings, bar, lead, character, half):
    base = plan(settings, bar)
    return base, apply_improvised_fill(
        base, settings, ImprovisationPhrase(lead=lead, character=character), half=half
    )


def test_improvised_leads_play_lines_shaped_by_their_character():
    bossa = MusicSettings(conductor=ConductorPlan(style="bossa"))
    lines = {}
    for character in ("space", "answer", "build", "resolve"):
        _, solo = improvise(bossa, 2, "piano", character, 0)
        lines[character] = [n for n in solo.notes if n.event_id.startswith("improv-piano-2-")]
        assert validate_notes(2, solo.notes) == solo.notes
    melody = {key: [n for n in notes if "shell" not in n.event_id] for key, notes in lines.items()}
    assert len(melody["space"]) < len(melody["answer"]) < len(melody["build"])
    rising = [note.pitch for note in melody["build"][:4]]
    assert rising == sorted(rising)
    _, ending = improvise(bossa, 3, "piano", "resolve", 1)
    last = [n for n in ending.notes if n.event_id.startswith("improv-piano-3-")]
    root = [n for n in last if "shell" not in n.event_id]
    assert len(root) == 1 and root[0].duration_ticks >= 2 * PPQ
    assert root[0].pitch % 12 == bossa.chord_at(3).root


def test_modern_band_solos_on_lead_guitar_and_bass_or_drums_take_their_turn():
    modern = rock()
    base, solo = improvise(modern, 4, "piano", "build", 1)
    guitar = part(solo, "lead_guitar")
    assert len(guitar) >= 5 and all(n.event_id.startswith("improv-") for n in guitar)
    assert part(solo, "piano") == part(base, "piano")  # keys keep comping
    _, bass_solo = improvise(modern, 4, "bass", "answer", 0)
    bass = part(bass_solo, "bass")
    assert bass[0].start_tick == 0 and bass[0].pitch % 12 == modern.chord_at(4).root
    assert all(40 <= n.pitch <= 55 for n in bass)
    _, fill = improvise(modern, 5, "drums", "answer", 1)
    toms = [n for n in part(fill, "drums") if n.pitch in {45, 47, 48, 50}]
    assert len(toms) == 4 and min(n.start_tick for n in toms) == 3 * PPQ


def test_watchdog_only_stops_the_program_it_was_given():
    child = subprocess.Popen(["/bin/sleep", "30"])
    parent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        # Watch on behalf of a throwaway parent, naming a program the child is not.
        script = (
            "import sys, time\n"
            "from band.process_watchdog import watch\n"
            f"watch({child.pid}, 'llama-server')\n"
            "time.sleep(30)\n"
        )
        owner = subprocess.Popen([sys.executable, "-c", script], cwd=os.getcwd())
        time.sleep(0.5)
        owner.kill()
        owner.wait()
        time.sleep(3)
        assert child.poll() is None, "a mismatched program must not be signalled"
        watcher = watch(child.pid, "sleep")
        release(watcher)
        assert watcher is None or watcher.poll() is not None
    finally:
        for process in (child, parent):
            process.kill()
            process.wait()


def test_startup_sweep_stops_only_orphaned_helpers(tmp_path):
    import shutil

    from band.process_watchdog import sweep

    # A real executable at its own path, as llama-server and scsynth are.
    source = tmp_path / "helper.c"
    source.write_text("#include <unistd.h>\nint main(void) { sleep(30); return 0; }\n")
    helper = tmp_path / "fake-helper"
    compiler = shutil.which("cc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("No C compiler to build a stand-in helper")
    subprocess.run([compiler, "-o", str(helper), str(source)], check=True)
    # A child of this test: its owner is alive, so the sweep must leave it running.
    owned = subprocess.Popen([str(helper), "30"])
    # An orphan: the shell that started it exits at once, so it is reparented to PID 1.
    subprocess.run(["/bin/sh", "-c", f'"{helper}" 30 >/dev/null 2>&1 & echo $!'], check=True)
    try:
        orphans = []
        for _ in range(50):
            listing = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,command="], check=False, capture_output=True, text=True
            ).stdout
            orphans = [
                int(line.split()[0])
                for line in listing.splitlines()
                if str(helper) in line and line.split()[1] == "1"
            ]
            if orphans:
                break
            time.sleep(0.05)
        assert orphans, "could not create an orphaned helper"
        assert sorted(sweep([helper])) == sorted(orphans)
        assert owned.poll() is None
    finally:
        owned.kill()
        owned.wait()
