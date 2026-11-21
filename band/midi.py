from collections.abc import Mapping, Sequence
from io import BytesIO

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

from .music import (
    KEYS_PROGRAMS,
    PPQ,
    TIME_SIGNATURES,
    TRACK_DEFINITIONS,
    BarPlan,
    MusicSettings,
    TrackSettings,
    active_tracks,
    section_volume,
)


def export_midi(
    settings: MusicSettings,
    tracks: Mapping[str, TrackSettings],
    plans: Sequence[BarPlan],
    *,
    use_score_tone: bool = True,
) -> bytes:
    """Serialize a performance with a conductor track and one GM track per active instrument."""
    if len({plan.bar for plan in plans}) != len(plans):
        raise ValueError("MIDI export accepts at most one plan per bar")
    midi = MidiFile(type=1, ticks_per_beat=PPQ)
    bar_count = max((plan.bar + 1 for plan in plans), default=len(settings.progression))
    by_bar = {plan.bar: plan for plan in plans}
    conductor = MidiTrack([MetaMessage("track_name", name="Conductor")])
    bar_starts = []
    end_tick = metadata_tick = 0
    previous_meter = previous_bpm = None
    for bar in range(bar_count):
        bar_starts.append(end_tick)
        plan = by_bar.get(bar)
        meter = plan.time_signature if plan and plan.time_signature else settings.meter
        bpm = plan.bpm if plan and plan.bpm else settings.bpm
        numerator, denominator = TIME_SIGNATURES[meter]
        if meter != previous_meter:
            conductor.append(
                MetaMessage(
                    "time_signature",
                    numerator=numerator,
                    denominator=denominator,
                    clocks_per_click=36 if meter == "6/8" else 24,
                    time=end_tick - metadata_tick,
                )
            )
            metadata_tick = end_tick
            previous_meter = meter
        if bpm != previous_bpm:
            conductor.append(
                MetaMessage("set_tempo", tempo=bpm2tempo(bpm), time=end_tick - metadata_tick)
            )
            metadata_tick = end_tick
            previous_bpm = bpm
        end_tick += PPQ * 4 * numerator // denominator
    conductor.append(MetaMessage("end_of_track", time=end_tick - metadata_tick))
    midi.tracks.append(conductor)
    for track_id in active_tracks(settings.lineup):
        definition = TRACK_DEFINITIONS[track_id]
        configuration = tracks[track_id]
        channel = definition.channel
        program = configuration.program
        if track_id == "piano" and use_score_tone:
            score_program = settings.score_keys_program(0)
            if score_program is not None:
                program = score_program
        midi_track = MidiTrack(
            [
                MetaMessage(
                    "track_name",
                    name="Keys"
                    if track_id == "piano" and settings.lineup == "modern"
                    else definition.title,
                ),
                Message("program_change", channel=channel, program=program),
                Message(
                    "control_change",
                    channel=channel,
                    control=7,
                    value=round(configuration.volume * 127),
                ),
            ]
        )
        events: list[tuple[int, int, int, int]] = []
        if song := settings.song:
            bar = 0
            previous_volume = configuration.volume
            previous_program = program
            for section in song.sections:
                volume = section_volume(section, track_id, configuration.volume)
                if volume != previous_volume and bar < len(bar_starts):
                    events.append((bar_starts[bar], 1, 7, round(volume * 127)))
                if track_id == "piano" and use_score_tone and bar < len(bar_starts):
                    section_program = KEYS_PROGRAMS[section.keys_tone]
                    if section_program != previous_program:
                        events.append((bar_starts[bar], 2, section_program, 0))
                    previous_program = section_program
                previous_volume = volume
                bar += section.bars
        if not configuration.muted:
            guitar_program = program
            for plan in sorted(plans, key=lambda item: item.bar):
                for note in plan.notes:
                    if note.track_id != track_id:
                        continue
                    tick = bar_starts[plan.bar] + note.start_tick
                    if track_id in {"rhythm_guitar", "lead_guitar"}:
                        played_program = program
                        if note.articulation == "palm_mute":
                            played_program = 28
                        elif note.articulation == "harmonic":
                            played_program = 31
                        if played_program != guitar_program:
                            events.append((tick, 2, played_program, 0))
                            guitar_program = played_program
                    events.append((tick, 3, note.pitch, note.velocity))
                    events.append((tick + note.duration_ticks, 0, note.pitch, 0))
        previous_tick = 0
        for tick, action, pitch, velocity in sorted(events):
            if action == 1:
                message = Message(
                    "control_change",
                    channel=channel,
                    control=pitch,
                    value=velocity,
                    time=tick - previous_tick,
                )
            elif action == 2:
                message = Message(
                    "program_change", channel=channel, program=pitch, time=tick - previous_tick
                )
            else:
                message = Message(
                    "note_on" if action == 3 else "note_off",
                    channel=channel,
                    note=pitch,
                    velocity=velocity,
                    time=tick - previous_tick,
                )
            midi_track.append(message)
            previous_tick = tick
        midi_track.append(MetaMessage("end_of_track", time=end_tick - previous_tick))
        midi.tracks.append(midi_track)
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()
