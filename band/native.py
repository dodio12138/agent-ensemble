"""Optional FluidSynth and SoundFont audio output."""

import ctypes as c
import os
import sys

from band.music import PLAYER_CHANNEL, TRACK_DEFINITIONS, guitar_program, slide_controls

NATIVE_LIBRARY = "libfluidsynth.dylib" if sys.platform == "darwin" else "libfluidsynth.so"


class FluidOutput:
    device = "fluidsynth"

    def __init__(self, library: str, soundfont: str, driver: str | None = None):
        self.library = c.CDLL(library)
        signatures = {
            "new_fluid_settings": (c.c_void_p,),
            "delete_fluid_settings": (None, c.c_void_p),
            "fluid_settings_setstr": (c.c_int, c.c_void_p, c.c_char_p, c.c_char_p),
            "new_fluid_synth": (c.c_void_p, c.c_void_p),
            "delete_fluid_synth": (None, c.c_void_p),
            "fluid_synth_sfload": (c.c_int, c.c_void_p, c.c_char_p, c.c_int),
            "fluid_synth_program_select": (c.c_int, c.c_void_p, *([c.c_int] * 4)),
            "fluid_synth_cc": (c.c_int, c.c_void_p, *([c.c_int] * 3)),
            "fluid_synth_noteon": (c.c_int, c.c_void_p, *([c.c_int] * 3)),
            "fluid_synth_noteoff": (c.c_int, c.c_void_p, c.c_int, c.c_int),
            "fluid_synth_all_notes_off": (c.c_int, c.c_void_p, c.c_int),
            "fluid_synth_all_sounds_off": (c.c_int, c.c_void_p, c.c_int),
            "new_fluid_audio_driver": (c.c_void_p, c.c_void_p, c.c_void_p),
            "delete_fluid_audio_driver": (None, c.c_void_p),
        }
        for name, (result, *arguments) in signatures.items():
            function = getattr(self.library, name)
            function.restype = result
            function.argtypes = arguments

        self.settings = self.library.new_fluid_settings()
        self.synth = self.driver = None
        self.programs: dict[int, int] = {}
        self.volumes: dict[int, int] = {}
        self.sliding: set[int] = set()
        try:
            if not self.settings:
                raise RuntimeError("FluidSynth could not allocate settings")
            if driver:
                status = self.library.fluid_settings_setstr(
                    self.settings, b"audio.driver", driver.encode()
                )
                if status < 0:
                    raise RuntimeError(f"Unknown FluidSynth driver: {driver}")
            self.synth = self.library.new_fluid_synth(self.settings)
            if not self.synth:
                raise RuntimeError("FluidSynth could not create the synthesizer")
            self.bank = self.library.fluid_synth_sfload(self.synth, os.fsencode(soundfont), 1)
            if self.bank < 0:
                raise RuntimeError(f"FluidSynth could not open {soundfont}")
            self.driver = self.library.new_fluid_audio_driver(self.settings, self.synth)
            if not self.driver:
                raise RuntimeError("FluidSynth could not open the audio device")
        except Exception:
            self.close()
            raise

    def deliver(self, event: dict) -> None:
        match event["type"]:
            case "note_on":
                track = event["track_id"]
                articulation = event.get("articulation")
                if track in {"rhythm_guitar", "lead_guitar"}:
                    event = {**event, "program": guitar_program(event["program"], articulation)}
                self._note_on(TRACK_DEFINITIONS[track].channel, event)
            case "note_off":
                channel = TRACK_DEFINITIONS[event["track_id"]].channel
                self.library.fluid_synth_noteoff(self.synth, channel, event["pitch"])
            case "silence":
                self.silence(event.get("track_id"))
            case "midi_on":
                self._note_on(PLAYER_CHANNEL, event)
            case "midi_off":
                self.library.fluid_synth_noteoff(self.synth, PLAYER_CHANNEL, event["pitch"])
            case "midi_silence":
                self._silence_channel(PLAYER_CHANNEL)
            case "midi_level":
                self._volume(PLAYER_CHANNEL, event["volume"])

    def _note_on(self, channel: int, event: dict) -> None:
        program = event["program"]
        if self.programs.get(channel) != program:
            bank = 128 if channel == 9 else 0
            result = self.library.fluid_synth_program_select(
                self.synth, channel, self.bank, bank, program
            )
            if result < 0:
                raise RuntimeError(f"Missing SoundFont preset {bank}:{program}")
            self.programs[channel] = program
        self._volume(channel, event.get("volume", 1))
        if event.get("slide_from") is not None or channel in self.sliding:
            for control, value in slide_controls(event.get("slide_from"), event.get("slide_ms")):
                self.library.fluid_synth_cc(self.synth, channel, control, value)
            if event.get("slide_from") is None:
                self.sliding.discard(channel)
            else:
                self.sliding.add(channel)
        self.library.fluid_synth_noteon(self.synth, channel, event["pitch"], event["velocity"])

    def _volume(self, channel: int, gain: float) -> None:
        volume = round(gain * 127)
        if self.volumes.get(channel) != volume:
            self.library.fluid_synth_cc(self.synth, channel, 7, volume)
            self.volumes[channel] = volume

    def _silence_channel(self, channel: int) -> None:
        self.library.fluid_synth_all_notes_off(self.synth, channel)
        self.library.fluid_synth_all_sounds_off(self.synth, channel)

    def silence(self, track_id: str | None = None) -> None:
        definitions = (TRACK_DEFINITIONS[track_id],) if track_id else TRACK_DEFINITIONS.values()
        for track in definitions:
            self._silence_channel(track.channel)

    def silence_all(self) -> None:
        self.silence()
        self._silence_channel(PLAYER_CHANNEL)

    def close(self) -> None:
        if self.synth:
            self.silence_all()
        if self.driver:
            self.library.delete_fluid_audio_driver(self.driver)
            self.driver = None
        if self.synth:
            self.library.delete_fluid_synth(self.synth)
            self.synth = None
        if self.settings:
            self.library.delete_fluid_settings(self.settings)
            self.settings = None
