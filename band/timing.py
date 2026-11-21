from __future__ import annotations

import heapq
import math
import threading
import time
from bisect import bisect_right
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from .audio_routing import AUDIO_EVENTS, AudioSource
from .improv_arrangement import bind_improvisation, phrase_for_bar, project_improvisation
from .music import (
    CHORUS_BARS,
    PPQ,
    TRACK_DEFINITIONS,
    BarPlan,
    Chord,
    Genre,
    Groove,
    GrooveTransition,
    ImprovisationPhrase,
    ImprovisationPlan,
    Lineup,
    MusicSettings,
    NegotiationEvent,
    Note,
    PlayerPhrase,
    Style,
    TimeSignature,
    TrackId,
    TrackSettings,
    active_tracks,
    parse_chord,
    section_volume,
)
from .precise_timer import LatenessStats
from .song import project_song
from .transition import apply_transition

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .planner import Planner
    from .precise_timer import PreciseTimer

NANOSECONDS_PER_MINUTE = 60_000_000_000
LOOKAHEAD_BARS = 2
Event = dict[str, Any]
MODERN_PROGRAMS = {"bass": 33, "piano": 4}
ADDED_TRACK_VOLUMES = {"rhythm_guitar": 0.65, "lead_guitar": 0.66, "synth": 0.45}


def _default_program(track_id: TrackId, lineup: Lineup) -> int:
    if lineup == "modern" and track_id in MODERN_PROGRAMS:
        return MODERN_PROGRAMS[track_id]
    return TRACK_DEFINITIONS[track_id].program


@dataclass(frozen=True)
class Meter:
    numerator: int = 4
    denominator: int = 4

    def __post_init__(self) -> None:
        if (
            self.numerator < 1
            or self.denominator < 1
            or self.denominator & (self.denominator - 1)
            or (PPQ * 4) % self.denominator
        ):
            raise ValueError("invalid meter for the configured tick resolution")

    @property
    def beat_ticks(self) -> int:
        return PPQ * 4 // self.denominator

    @property
    def bar_ticks(self) -> int:
        return self.numerator * self.beat_ticks

    def to_tick(self, bar: int, beat: int, tick: int = 0) -> int:
        if bar < 0 or not 0 <= beat < self.numerator or not 0 <= tick < self.beat_ticks:
            raise ValueError("invalid musical position")
        return bar * self.bar_ticks + beat * self.beat_ticks + tick

    def from_tick(self, tick: int) -> tuple[int, int, int]:
        if tick < 0:
            raise ValueError("tick must be nonnegative")
        bar, within_bar = divmod(tick, self.bar_ticks)
        beat, within_beat = divmod(within_bar, self.beat_ticks)
        return bar, beat, within_beat


class TempoMap:
    """Piecewise quarter-note tempo with exact accumulation at each change."""

    def __init__(self, changes: tuple[tuple[int, int], ...] = ((0, 120),)) -> None:
        self._ticks = tuple(tick for tick, _ in changes)
        if (
            not self._ticks
            or self._ticks[0] != 0
            or any(left >= right for left, right in zip(self._ticks, self._ticks[1:]))
            or any(bpm <= 0 for _, bpm in changes)
        ):
            raise ValueError("tempo changes must start at zero and increase with positive BPM")
        self._rates = tuple(self.ns_per_tick(bpm) for _, bpm in changes)
        elapsed = [Fraction(0)]
        for index, tick in enumerate(self._ticks[1:]):
            elapsed.append(elapsed[-1] + (tick - self._ticks[index]) * self._rates[index])
        self._elapsed = tuple(elapsed)

    @staticmethod
    def ns_per_tick(bpm: int) -> Fraction:
        return Fraction(NANOSECONDS_PER_MINUTE, bpm * PPQ)

    def tick_to_ns(self, tick: int) -> int:
        if tick < 0:
            raise ValueError("tick must be nonnegative")
        index = bisect_right(self._ticks, tick) - 1
        return math.ceil(self._elapsed[index] + (tick - self._ticks[index]) * self._rates[index])

    def ns_to_tick(self, elapsed_ns: int) -> int:
        if elapsed_ns < 0:
            raise ValueError("elapsed nanoseconds must be nonnegative")
        index = bisect_right(self._elapsed, elapsed_ns) - 1
        return self._ticks[index] + (elapsed_ns - self._elapsed[index]) // self._rates[index]


@dataclass(frozen=True)
class Configuration:
    settings: MusicSettings
    tracks: Mapping[str, TrackSettings]
    version: int
    fallback: tuple[BarPlan, ...]
    program_overrides: frozenset[str] = frozenset()
    song_origin: int = 0


@dataclass(frozen=True)
class PreparedCue:
    transition: GrooveTransition
    fallback: BarPlan


@dataclass(frozen=True)
class ScheduledImprovisation:
    start_bar: int
    end_bar: int
    variant: int
    plan: ImprovisationPlan
    fallback: tuple[BarPlan, ...]
    metadata: Event

    def snapshot(self) -> Event:
        return {
            "start_bar": self.start_bar,
            "end_bar": self.end_bar,
            "plan": self.plan.model_dump(mode="json"),
            **self.metadata,
        }


class Scheduler:
    """Owns the audible timeline independently of HTTP, WebSocket and agent tasks.

    The optional output callback is an in-process handoff and must enqueue without
    performing network, disk, or device I/O. In manual mode, a fake monotonic clock
    and pump() exercise the same transition and dispatch logic as the timer thread.
    """

    def __init__(
        self,
        planner: Planner,
        settings: MusicSettings | None = None,
        *,
        clock: Callable[[], int] = time.perf_counter_ns,
        threaded: bool = True,
        output: Callable[[Event], None] | None = None,
        history_size: int = 4096,
        audio_fallback: AudioSource = "browser",
        audio_preferred: AudioSource = "browser",
        audio_reason: str | None = None,
        timer: PreciseTimer | None = None,
    ) -> None:
        self._planner = planner
        # The native timer shares perf_counter's clock, so a fake test clock never uses it.
        self._timer = timer if clock is time.perf_counter_ns else None
        self._lateness = LatenessStats()
        # Optional hook (bar start ns, bar length ns) -> corrected start, e.g. Ableton Link.
        self.phase_aligner: Callable[[int, int], int] | None = None
        self._clock = clock
        self._output = output
        self._condition = threading.Condition()
        self._configuration_lock = threading.Lock()
        self._transport = "stopped"
        self._bar = 0
        self._bar_start = Fraction(0)
        self._bar_start_tick = 0
        self._rate = TempoMap.ns_per_tick(120)
        self._scheduled: list[tuple[int, int, int, Note]] = []
        self._active: dict[str, Note] = {}
        self._drafts: dict[int, BarPlan] = {}
        self._draft_player: dict[int, int] = {}
        self._player_phrase: PlayerPhrase | None = None
        self._player_version = 0
        self._locked: dict[int, BarPlan] = {}
        self._locked_until = -1
        self._pending: dict[int, Configuration] = {}
        self._cues: dict[int, PreparedCue] = {}
        self._improvisations: list[ScheduledImprovisation] = []
        self._sequence = 0
        self._version_counter = 0
        self._events: deque[Event] = deque(maxlen=history_size)
        self._negotiation: deque[Event] = deque(maxlen=42)
        self._audio_fallback = audio_fallback
        self._audio_preferred = audio_preferred
        self._audio_output = audio_fallback
        self._audio_reason = audio_reason
        self._sounding: dict[str, Event] = {}
        self._closed = False
        settings = settings or MusicSettings()
        self._performing, self._section, _ = project_song(settings, 0, 0)
        tracks = {
            key: TrackSettings(
                program=_default_program(key, settings.lineup),
                volume=ADDED_TRACK_VOLUMES.get(key, 0.8),
            )
            for key in TRACK_DEFINITIONS
        }
        self._configuration = Configuration(
            settings, MappingProxyType(tracks), 0, self._fallback_patterns(settings, 0)
        )
        self._thread: threading.Thread | None = None
        if threaded:
            self._thread = threading.Thread(target=self._run, name="band-scheduler", daemon=True)
            self._thread.start()

    def _fallback_patterns(
        self, settings: MusicSettings, version: int, origin: int = 0
    ) -> tuple[BarPlan, ...]:
        if settings.song is None:
            return tuple(
                self._planner.fallback(bar, settings, version) for bar in range(CHORUS_BARS)
            )
        song = settings.song
        return tuple(
            self._song_fallback(bar, settings, version, origin)
            for bar in range(origin, origin + song.total_bars)
        )

    def _song_fallback(
        self, bar: int, settings: MusicSettings, version: int, origin: int
    ) -> BarPlan:
        projected, section, offset = project_song(settings, origin, bar)
        previous, _, _ = project_song(settings, origin, bar - 1)
        following, _, _ = project_song(settings, origin, bar + 1)
        plan = self._planner.fallback(
            bar,
            projected,
            version,
            voice_leading=True,
            section=section,
            section_bar=offset,
            prior_chord=previous.chord_at(bar - 1),
            next_chord=following.chord_at(bar + 1),
        ).model_copy(
            update={
                "harmony": projected.chord_at(bar).symbol,
                "bpm": projected.bpm,
                "time_signature": projected.meter,
            }
        )
        song = settings.song
        assert song is not None and section is not None
        end_of_song = (bar + 1 - origin) % song.total_bars == 0
        if (
            offset + 1 == section.bars
            and (settings.loop or not end_of_song)
            and (cue := self._song_cue(projected, following, bar + 1, "pickup"))
        ):
            return apply_transition(plan, projected, cue)
        if (
            offset == 0
            and (bar != origin or settings.loop)
            and (cue := self._song_cue(previous, projected, bar, "arrival"))
        ):
            return apply_transition(plan, projected, cue)
        return plan

    @staticmethod
    def _song_cue(
        source: MusicSettings, target: MusicSettings, bar: int, phase: Literal["pickup", "arrival"]
    ) -> GrooveTransition | None:
        if (source.bpm, source.meter) == (target.bpm, target.meter):
            return None
        return GrooveTransition(
            phase=phase,
            from_style=source.conductor.style,
            to_style=target.conductor.style,
            from_bpm=source.bpm,
            to_bpm=target.bpm,
            from_meter=source.meter,
            to_meter=target.meter,
            next_chord=target.chord_at(bar).symbol,
        )

    def _record(self, event_type: str, *, at_ns: int | None = None, **fields: Any) -> None:
        self._sequence += 1
        event = {
            "type": event_type,
            "sequence": self._sequence,
            "time_ns": self._clock() if at_ns is None else at_ns,
            "bar": self._bar,
            **fields,
        }
        if event_type in AUDIO_EVENTS:
            event["audio_output"] = self._audio_output
            self._track_audio(event)
        self._events.append(event)
        if event_type == "negotiation":
            self._negotiation.append(event)
        if self._output is not None:
            try:
                self._output(event)
            except Exception as error:  # noqa: BLE001 - isolate a failed output handoff
                self._sequence += 1
                self._events.append(
                    {
                        "type": "output_error",
                        "sequence": self._sequence,
                        "time_ns": self._clock(),
                        "bar": self._bar,
                        "sequence_ref": event["sequence"],
                        "reason": type(error).__name__,
                    }
                )

    def _track_audio(self, event: Event) -> None:
        match event["type"]:
            case "midi_on" | "note_on":
                self._sounding[event["event_id"]] = event
            case "midi_off" | "note_off":
                self._sounding.pop(event["event_id"], None)
            case "silence":
                track = event.get("track_id")
                self._sounding = {
                    key: held
                    for key, held in self._sounding.items()
                    if held["type"] == "midi_on" or (track and held.get("track_id") != track)
                }
            case "midi_silence":
                self._sounding = {
                    key: held for key, held in self._sounding.items() if held["type"] != "midi_on"
                }
            case "midi_level":
                for key, held in self._sounding.items():
                    if held["type"] == "midi_on":
                        self._sounding[key] = {**held, "volume": event["volume"]}

    def _resumable_audio(self) -> list[Event]:
        return [
            {**event, "audio_output": self._audio_output}
            for event in self._sounding.values()
            if event.get("track_id") != "drums"
        ]

    def switch_audio(self, route: AudioSource, reason: str | None) -> None:
        """Record a source boundary with exactly the notes that are still sounding."""
        with self._condition:
            if self._closed or (route, reason) == (self._audio_output, self._audio_reason):
                return
            changed = route != self._audio_output
            self._audio_output = route
            self._audio_reason = reason
            if changed:
                self._record(
                    "audio_route", audio_output=route, reason=reason, resume=self._resumable_audio()
                )
            else:
                self._record("audio_status", audio_output=route, reason=reason)

    def _desired(self) -> Configuration:
        return self._pending[max(self._pending)] if self._pending else self._configuration

    def desired_configuration(self) -> tuple[MusicSettings, dict[str, TrackSettings]]:
        with self._condition:
            configuration = self._desired()
            return configuration.settings, dict(configuration.tracks)

    def _for_bar(self, bar: int) -> Configuration:
        effective = max((target for target in self._pending if target <= bar), default=None)
        return self._configuration if effective is None else self._pending[effective]

    def _bar_settings(self, bar: int) -> MusicSettings:
        config = self._for_bar(bar)
        return project_song(config.settings, config.song_origin, bar)[0]

    def song_origin(self, *, desired: bool = False) -> int:
        with self._condition:
            return (self._desired() if desired else self._configuration).song_origin

    def _active_settings(self) -> MusicSettings:
        return self._performing

    def _deadline(self, tick: int) -> int:
        return math.ceil(self._bar_start + tick * self._rate)

    def _first_editable_bar(self) -> int:
        if self._transport == "stopped":
            return 0
        bar = self._locked_until + 1
        if self._transport == "playing" and self._clock() >= self._deadline(self._lock_tick()):
            bar = max(bar, self._bar + 2)
        return bar

    def _lock_tick(self) -> int:
        settings = self._active_settings()
        return settings.bar_ticks - settings.pulse_ticks

    def stage_settings(self, patch: dict[str, Any], *, next_chorus: bool = False) -> Event:
        return self._stage(patch, None, next_chorus=next_chorus)

    def record_remote_agent(self, track: TrackId, status: str, **fields: Any) -> None:
        with self._condition:
            self._record("remote_agent", track_id=track, status=status, **fields)

    def record_conductor(self, result: Event, **context: Any) -> None:
        with self._condition:
            self._record("conductor", **result, **context)

    def record_improvisation(self, status: str, **fields: Any) -> None:
        with self._condition:
            self._record("improvisation", status=status, **fields)

    def _improvisation_for(self, bar: int) -> ScheduledImprovisation | None:
        return next(
            (item for item in self._improvisations if item.start_bar <= bar < item.end_bar),
            None,
        )

    def arrangement_for(
        self,
        bar: int,
        settings: MusicSettings,
    ) -> tuple[MusicSettings, int | None, ImprovisationPhrase | None, dict[str, Any]]:
        with self._condition:
            chorus = self._improvisation_for(bar)
            prior = self._improvisation_for(bar - 1) if bar else None
            following = self._improvisation_for(bar + 1)
            configuration = self._for_bar(bar)
            _, section, offset = project_song(settings, configuration.song_origin, bar)
            connects = bool(chorus or prior or following or section)
            options = (
                {
                    "voice_leading": True,
                    "prior_chord": self._performance_chord(bar - 1, prior) if bar else None,
                    "next_chord": (
                        self._performance_chord(bar + 1, following)
                        if self._continues_to(bar + 1)
                        else None
                    ),
                }
                if connects
                else {}
            )
            if section is not None:
                options.update(section=section, section_bar=offset)
        if chorus is None:
            return settings, None, None, options
        projected = project_improvisation(
            settings, bar, chorus.start_bar, chorus.plan, chorus.variant
        )
        return (
            projected,
            chorus.variant,
            phrase_for_bar(chorus.plan, bar, chorus.start_bar),
            options,
        )

    def _performance_chord(self, bar: int, chorus: ScheduledImprovisation | None) -> Chord:
        if (locked := self._locked.get(bar)) and locked.harmony:
            return parse_chord(locked.harmony)
        if chorus is not None:
            return parse_chord(chorus.plan.chord_for_bar(bar, chorus.start_bar))
        return self._bar_settings(bar).chord_at(bar)

    def wants_improvisation(self) -> bool:
        with self._condition:
            if self._transport != "playing":
                return False
            if any(item.start_bar > self._bar for item in self._improvisations):
                return False
            if self._bar_settings(self._bar).song and not self._song_improv_open(self._bar):
                return False
            start, end = self._improvisation_window()
            if start >= end:
                return False
            live = self._improvisation_for(self._bar)
            return live is None or live.end_bar - self._bar <= 4

    def _improvisation_window(self) -> tuple[int, int]:
        first = 0 if self._transport == "stopped" else self._first_editable_bar()
        locked = self._improvisation_for(first - 1)
        start = max(first, locked.end_bar if locked else 0)
        end = start + CHORUS_BARS
        future = min((bar for bar in self._pending if bar > start), default=end)
        end = min(end, future)
        configuration = self._for_bar(start)
        if configuration.settings.song is not None:
            offset = start - configuration.song_origin
            song = configuration.settings.song
            if not configuration.settings.loop and offset >= song.total_bars:
                return start, start
            _, section_start, section = song.section_at(offset)
            remaining = section_start + section.bars - offset % song.total_bars
            return start, min(end, start + remaining)
        stop_at = (self._bar // CHORUS_BARS + 1) * CHORUS_BARS
        if not self._continues_to(stop_at):
            end = min(end, stop_at)
        return start, end

    def _song_improv_open(self, bar: int) -> bool:
        configuration = self._for_bar(bar)
        _, section, _ = project_song(configuration.settings, configuration.song_origin, bar)
        return section is None or section.role in {"a", "b", "bridge"}

    def improvisation_theme(self) -> MusicSettings:
        with self._condition:
            start, _ = self._improvisation_window()
            return self._bar_settings(start)

    def schedule_improvisation(
        self,
        plan: ImprovisationPlan,
        variant: int,
        theme: MusicSettings,
        metadata: Event,
    ) -> ScheduledImprovisation | None:
        for _ in range(3):
            with self._condition:
                if self._closed:
                    return None
                start, end = self._improvisation_window()
                desired = self._bar_settings(start)
                if (
                    theme.progression != desired.progression
                    or self._groove(theme) != self._groove(desired)
                    or theme.conductor.profile != desired.conductor.profile
                    or theme.lineup != desired.lineup
                    or theme.song != desired.song
                ):
                    return None
                if start >= end:
                    return None
                configurations = tuple(self._for_bar(bar) for bar in range(start, end))
                bound = bind_improvisation(plan, theme, start, end)
                prior = (
                    self._performance_chord(start - 1, self._improvisation_for(start - 1))
                    if start
                    else None
                )
                following = (
                    self._bar_settings(end).chord_at(end) if self._continues_to(end) else None
                )
            backup = []
            for bar, configuration in zip(range(start, end), configurations, strict=True):
                performance, section, offset = project_song(
                    configuration.settings, configuration.song_origin, bar
                )
                projected = project_improvisation(performance, bar, start, bound, variant)
                backup.append(
                    self._planner.fallback(
                        bar,
                        projected,
                        configuration.version,
                        voice_leading=True,
                        prior_chord=prior if bar == start else projected.chord_at(bar - 1),
                        next_chord=following if bar + 1 == end else projected.chord_at(bar + 1),
                        section=section,
                        section_bar=offset,
                    ).model_copy(
                        update={
                            "harmony": projected.chord_at(bar).symbol,
                            "improvisation_id": variant,
                            "improvisation_phrase": phrase_for_bar(bound, bar, start),
                        },
                    )
                )
            with self._condition:
                if self._closed:
                    return None
                if (start, end) != self._improvisation_window() or configurations != tuple(
                    self._for_bar(bar) for bar in range(start, end)
                ):
                    continue
                chorus = ScheduledImprovisation(start, end, variant, bound, tuple(backup), metadata)
                self._cut_improvisation(start)
                self._improvisations.append(chorus)
                return chorus
        return None

    def _cut_improvisation(self, bar: int) -> None:
        self._improvisations = [
            replace(item, end_bar=min(item.end_bar, bar))
            for item in self._improvisations
            if item.start_bar < bar
        ]

    def cancel_queued_automatic_improvisation(self) -> None:
        with self._condition:
            first = self._first_editable_bar()
            self._improvisations = [
                item
                for item in self._improvisations
                if item.start_bar < first or not item.metadata.get("automatic")
            ]

    def record_midi(self, event_type: str, **fields: Any) -> None:
        with self._condition:
            if not self._closed:
                self._record(event_type, **fields)

    def position(self) -> tuple[str, int, int]:
        with self._condition:
            return self._transport, self._bar, self._tick()

    def midi_position(self) -> tuple[str, int, int, MusicSettings]:
        with self._condition:
            tick = self._tick()
            return self._transport, self._bar_start_tick + tick, tick, self._active_settings()

    def player_context(self) -> tuple[PlayerPhrase | None, int]:
        with self._condition:
            return self._player_phrase, self._player_version

    def update_player_phrase(self, phrase: PlayerPhrase | None) -> None:
        with self._condition:
            if self._transport != "playing":
                phrase = None
            if phrase != self._player_phrase:
                self._player_phrase = phrase
                self._player_version += 1

    def record_negotiation(self, target: int, version: int, event: NegotiationEvent) -> None:
        with self._condition:
            if self._closed or self._for_bar(target).version != version:
                return
            self._record(
                "negotiation",
                target_bar=target,
                state_version=version,
                time_signature=self._bar_settings(target).meter,
                **event.model_dump(mode="json"),
            )

    def stage_track(self, track_id: str, patch: dict[str, Any]) -> Event:
        if track_id not in TRACK_DEFINITIONS:
            raise KeyError(track_id)
        return self._stage(patch, track_id)

    def _stage(
        self, patch: dict[str, Any], track_id: str | None, *, next_chorus: bool = False
    ) -> Event:
        with self._configuration_lock:
            with self._condition:
                self._check_open()
                desired = self._desired()
            settings = desired.settings
            tracks = dict(desired.tracks)
            program_overrides = desired.program_overrides
            if track_id is None:
                settings = MusicSettings.model_validate({**settings.model_dump(), **patch})
                if settings.lineup != desired.settings.lineup:
                    for key in MODERN_PROGRAMS.keys() - program_overrides:
                        tracks[key] = tracks[key].model_copy(
                            update={"program": _default_program(key, settings.lineup)}
                        )
                profile = settings.conductor.profile
                if (
                    "conductor" not in patch
                    and settings.progression != desired.settings.progression
                    and profile is not None
                    and profile.harmony != "keep"
                ):
                    conductor = settings.conductor.model_copy(
                        update={"profile": profile.model_copy(update={"harmony": "keep"})}
                    )
                    settings = settings.model_copy(update={"conductor": conductor})
            else:
                tracks[track_id] = TrackSettings.model_validate(
                    {**tracks[track_id].model_dump(), **patch}
                )
                if "program" in patch:
                    program_overrides = program_overrides | {track_id}
            if (
                settings == desired.settings
                and tracks == desired.tracks
                and program_overrides == desired.program_overrides
            ):
                return self.snapshot()
            fallback = desired.fallback
            if settings != desired.settings:
                fallback = self._fallback_patterns(settings, desired.version + 1)
            with self._condition:
                self._check_open()
                self._version_counter += 1
                configuration = Configuration(
                    settings,
                    MappingProxyType(tracks),
                    self._version_counter,
                    fallback,
                    program_overrides,
                    desired.song_origin,
                )
                first = self._first_editable_bar()
                last = max(self._pending, default=-1)
                bar = max(first, last)
                song_changed = settings.song != desired.settings.song
                if self._transport == "stopped":
                    if (
                        song_changed
                        or settings.lineup != desired.settings.lineup
                        or settings.progression != desired.settings.progression
                        or self._groove(settings) != self._groove(desired.settings)
                    ):
                        self._cut_improvisation(0)
                    self._configuration = configuration
                    self._performing, self._section, _ = project_song(
                        settings, configuration.song_origin, 0
                    )
                    self._drafts.clear()
                else:
                    structural = song_changed or self._groove(settings) != self._groove(
                        desired.settings
                    )
                    if structural and not (last in self._cues and last - 1 >= first):
                        # Allow an entire editable bar for the pickup, even at the lock boundary.
                        bar = max(first + 1, last + 2)
                    if next_chorus:
                        bar = max(bar, (self._bar // CHORUS_BARS + 1) * CHORUS_BARS)
                        bar = (bar + CHORUS_BARS - 1) // CHORUS_BARS * CHORUS_BARS
                    if structural or settings.progression != desired.settings.progression:
                        self._cut_improvisation(bar - 1 if structural and next_chorus else first)
                    elif settings.lineup != desired.settings.lineup:
                        self._cut_improvisation(bar)
                    if song_changed:
                        configuration = replace(configuration, song_origin=bar)
                    invalidate = bar
                    if structural or bar in self._cues:
                        invalidate = self._prepare_cues(bar, first, configuration)
                    self._pending[bar] = configuration
                    self._drafts = {
                        key: plan for key, plan in self._drafts.items() if key < invalidate
                    }
                self._draft_player = {
                    key: stamp for key, stamp in self._draft_player.items() if key in self._drafts
                }
                self._record(
                    "configuration",
                    target_bar=bar,
                    state_version=configuration.version,
                    status="applied" if self._transport == "stopped" else "pending",
                )
                self._condition.notify()
                return self._snapshot()

    @staticmethod
    def _groove(
        settings: MusicSettings,
    ) -> tuple[Style, int, Genre | None, Groove | None, TimeSignature]:
        plan = settings.conductor
        profile = plan.profile
        return (
            plan.style,
            settings.bpm,
            profile and profile.genre,
            profile and profile.groove,
            settings.meter,
        )

    @staticmethod
    def _materialize_fallback(bar: int, configuration: Configuration) -> BarPlan:
        offset = bar - configuration.song_origin if configuration.settings.song else bar
        template = configuration.fallback[offset % len(configuration.fallback)]
        notes = tuple(
            note.model_copy(update={"bar": bar, "event_id": f"fallback:{bar}:{note.event_id}"})
            for note in template.notes
        )
        return template.model_copy(
            update={"bar": bar, "version": configuration.version, "notes": notes}
        )

    def export_context(self) -> tuple[MusicSettings, dict[str, TrackSettings], bool]:
        with self._condition:
            desired = self._desired()
            return desired.settings, dict(desired.tracks), "piano" not in desired.program_overrides

    def _transition_for(self, bar: int, configuration: Configuration) -> GrooveTransition | None:
        if cue := self._cues.get(bar):
            return cue.transition
        if configuration.settings.song is None:
            return None
        template = configuration.fallback[
            (bar - configuration.song_origin) % len(configuration.fallback)
        ]
        return template.transition

    def _prepare_cues(self, arrival: int, first: int, target: Configuration) -> int:
        pickup = arrival - 1
        source = self._for_bar(pickup)
        source_settings = self._bar_settings(pickup)
        target_settings = project_song(target.settings, target.song_origin, arrival)[0]
        scheduled = self._cues.get(arrival)
        # A pickup that has locked already told the band how to enter.
        if scheduled is not None and pickup < first:
            from_style = scheduled.transition.from_style
            from_bpm = scheduled.transition.from_bpm
            from_genre = scheduled.transition.from_genre
            from_groove = scheduled.transition.from_groove
            from_meter = scheduled.transition.from_meter
        else:
            from_style, from_bpm, from_genre, from_groove, from_meter = self._groove(
                source_settings
            )
        incoming = self._groove(target_settings)
        to_style, to_bpm, to_genre, to_groove, to_meter = incoming
        writable = [
            (bar, phase, config)
            for bar, phase, config in ((pickup, "pickup", source), (arrival, "arrival", target))
            if bar >= first
        ]
        prepared = {}
        if (from_style, from_bpm, from_genre, from_groove, from_meter) != incoming and (
            target.settings.song is not None or arrival % CHORUS_BARS or target.settings.loop
        ):
            next_chord = target_settings.chord_at(arrival)
            for bar, phase, config in writable:
                cue = GrooveTransition(
                    phase=phase,
                    from_style=from_style,
                    to_style=to_style,
                    from_bpm=from_bpm,
                    to_bpm=to_bpm,
                    from_meter=from_meter,
                    to_meter=to_meter,
                    from_genre=from_genre,
                    to_genre=to_genre,
                    from_groove=from_groove,
                    to_groove=to_groove,
                    next_chord=next_chord.symbol,
                )
                fallback = apply_transition(
                    self._materialize_fallback(bar, config),
                    source_settings if phase == "pickup" else target_settings,
                    cue,
                )
                prepared[bar] = PreparedCue(cue, fallback)
        invalidate = arrival
        for bar, _, _ in writable:
            if prepared.get(bar) != self._cues.get(bar):
                invalidate = min(invalidate, bar)
            self._cues.pop(bar, None)
        self._cues.update(prepared)
        return invalidate

    def _continues_to(self, bar: int) -> bool:
        configuration = self._for_bar(bar)
        settings = configuration.settings
        if settings.song is not None:
            return settings.loop or bar < configuration.song_origin + settings.song.total_bars
        return bar % CHORUS_BARS != 0 or settings.loop

    def planning_targets(self) -> tuple[tuple[int, MusicSettings, int], ...]:
        with self._condition:
            return self._targets()

    def _targets(self) -> tuple[tuple[int, MusicSettings, int], ...]:
        if self._closed:
            return ()
        targets = []
        first = self._first_editable_bar()
        for bar in range(self._bar, self._bar + LOOKAHEAD_BARS + 1):
            if bar > self._bar and not self._continues_to(bar):
                break
            if bar < first:
                continue
            configuration = self._for_bar(bar)
            plan = self._drafts.get(bar)
            chorus = self._improvisation_for(bar)
            if (
                plan is None
                or plan.version != configuration.version
                or plan.revision == 0
                or self._draft_player.get(bar) != self._player_version
                or plan.improvisation_id != (chorus.variant if chorus else None)
            ):
                targets.append((bar, self._bar_settings(bar), configuration.version))
        return tuple(targets)

    def commit(self, plan: BarPlan, *, player_version: int | None = None) -> bool:
        with self._condition:
            configuration = self._for_bar(plan.bar)
            settings = self._bar_settings(plan.bar)
            cue = self._transition_for(plan.bar, configuration)
        bar_ticks = settings.bar_ticks
        fits = all(note.start_tick + note.duration_ticks <= bar_ticks for note in plan.notes)
        if fits and cue and plan.transition is None and plan.version == configuration.version:
            plan = apply_transition(plan, settings, cue)
        with self._condition:
            if self._closed:
                return False
            configuration = self._for_bar(plan.bar)
            settings = self._bar_settings(plan.bar)
            cue = self._transition_for(plan.bar, configuration)
            chorus = self._improvisation_for(plan.bar)
            current = self._drafts.get(plan.bar)
            reason = None
            if plan.bar < self._first_editable_bar():
                reason = "locked"
            elif plan.bar > self._bar + LOOKAHEAD_BARS:
                reason = "outside_lookahead"
            elif plan.version != configuration.version:
                reason = "stale_state"
            elif any(
                note.start_tick + note.duration_ticks > settings.bar_ticks for note in plan.notes
            ):
                reason = "outside_meter"
            elif any(
                note.track_id not in active_tracks(configuration.settings.lineup)
                for note in plan.notes
            ):
                reason = "inactive_track"
            elif player_version is not None and player_version != self._player_version:
                reason = "stale_player"
            elif plan.improvisation_id != (chorus.variant if chorus else None):
                reason = "stale_improvisation"
            elif plan.transition != cue:
                reason = "stale_transition"
            elif (
                current is not None
                and current.version == plan.version
                and current.revision >= plan.revision
                and self._draft_player.get(plan.bar) == self._player_version
                and current.improvisation_id == plan.improvisation_id
            ):
                reason = "stale_revision"
            else:
                event_ids = {note.event_id for note in plan.notes}
                for other in (*self._drafts.values(), *self._locked.values()):
                    if other.bar != plan.bar and any(
                        note.event_id in event_ids for note in other.notes
                    ):
                        reason = "duplicate_event"
                        break
            if reason is not None:
                self._record(
                    "plan",
                    status="rejected",
                    target_bar=plan.bar,
                    reason=reason,
                    state_version=plan.version,
                    revision=plan.revision,
                )
                return False
            self._drafts[plan.bar] = plan
            self._draft_player[plan.bar] = self._player_version
            self._record_plan(plan, "committed")
            return True

    def _record_plan(self, plan: BarPlan, status: str) -> None:
        self._record(
            "plan",
            status=status,
            target_bar=plan.bar,
            state_version=plan.version,
            revision=plan.revision,
            harmony=plan.harmony,
            improvisation_id=plan.improvisation_id,
            fallback_tracks=list(plan.fallback_tracks),
            intents=[intent.model_dump(mode="json") for intent in plan.intents],
            transition=plan.transition.model_dump(mode="json") if plan.transition else None,
            player_response=(
                plan.player_response.model_dump(mode="json") if plan.player_response else None
            ),
        )

    def _lock_bar(self, bar: int) -> BarPlan:
        if bar in self._locked:
            return self._locked[bar]
        configuration = self._for_bar(bar)
        cue = self._cues.get(bar)
        fallback = cue.fallback if cue else self._materialize_fallback(bar, configuration)
        chorus = self._improvisation_for(bar)
        plan = self._drafts.pop(bar, None)
        self._draft_player.pop(bar, None)
        if (
            plan is None
            or plan.version != configuration.version
            or plan.transition != fallback.transition
            or plan.improvisation_id != (chorus.variant if chorus else None)
        ):
            if chorus and not cue:
                plan = chorus.fallback[bar - chorus.start_bar].model_copy(
                    update={"version": configuration.version},
                )
                if fallback.transition:
                    plan = apply_transition(plan, self._bar_settings(bar), fallback.transition)
            else:
                plan = fallback
        self._locked[bar] = plan
        self._locked_until = max(bar, self._locked_until)
        self._record_plan(plan, "locked")
        return plan

    def _enter_bar(self, bar: int, start: Fraction) -> None:
        self._bar = bar
        self._bar_start = start
        self._improvisations = [item for item in self._improvisations if item.end_bar > bar]
        if bar in self._pending:
            previous_lineup = self._configuration.settings.lineup
            previous_tracks = self._configuration.tracks
            self._configuration = self._pending.pop(bar)
            self._record(
                "configuration",
                target_bar=bar,
                status="applied",
                state_version=self._configuration.version,
            )
            current_tracks = active_tracks(self._configuration.settings.lineup)
            for track_id in active_tracks(previous_lineup):
                if track_id not in current_tracks:
                    self._record("silence", track_id=track_id, reason="lineup")
            for track_id in current_tracks:
                track = self._configuration.tracks[track_id]
                if track.muted and not previous_tracks[track_id].muted:
                    self._record("silence", track_id=track_id, reason="mute")
        settings, self._section, _ = project_song(
            self._configuration.settings, self._configuration.song_origin, bar
        )
        self._performing = settings
        self._rate = TempoMap.ns_per_tick(settings.bpm)
        plan = self._lock_bar(bar)
        self._cues = {key: cue for key, cue in self._cues.items() if key >= bar}
        self._locked = {key: locked for key, locked in self._locked.items() if key >= bar}
        self._scheduled = []
        for index, note in enumerate(plan.notes):
            self._scheduled.append((note.start_tick, 1, index, note))
            self._scheduled.append((note.start_tick + note.duration_ticks, 0, index, note))
        heapq.heapify(self._scheduled)
        self._record(
            "bar",
            bpm=settings.bpm,
            time_signature=settings.meter,
            meter=self._meter_snapshot(settings),
            due_ns=math.ceil(start),
            state_version=self._configuration.version,
            transition=plan.transition.model_dump(mode="json") if plan.transition else None,
        )

    def _note_event(self, note: Note, kind: str, due_ns: int, at_ns: int, reason: str) -> None:
        track = self._configuration.tracks[note.track_id]
        program, volume = track.program, track.volume
        if note.track_id == "piano" and "piano" not in self._configuration.program_overrides:
            program = self._performing.score_keys_program(
                self._bar - self._configuration.song_origin
            )
            if program is None:
                program = track.program
        if section := self._section:
            volume = section_volume(section, note.track_id, volume)
        if kind == "note_on" and reason == "scheduled":
            self._lateness.add(max(0, at_ns - due_ns))
        self._record(
            kind,
            at_ns=at_ns,
            due_ns=due_ns,
            late_ns=max(0, at_ns - due_ns),
            reason=reason,
            event_id=note.event_id,
            track_id=note.track_id,
            channel=note.channel,
            pitch=note.pitch,
            velocity=note.velocity if kind == "note_on" else 0,
            program=program,
            volume=volume,
            start_tick=note.start_tick,
            duration_ticks=note.duration_ticks,
            **({"articulation": note.articulation} if note.articulation != "normal" else {}),
        )

    def _dispatch(self, note: Note, action: int, due_ns: int, now: int) -> None:
        if action == 0:
            if self._active.pop(note.event_id, None) is not None:
                self._note_event(note, "note_off", due_ns, now, "scheduled")
            return
        track = self._configuration.tracks[note.track_id]
        if track.muted:
            return
        if now >= self._deadline(note.start_tick + note.duration_ticks):
            self._record(
                "dropped_note",
                at_ns=now,
                due_ns=due_ns,
                track_id=note.track_id,
                event_id=note.event_id,
                reason="past_end",
            )
            return
        self._active[note.event_id] = note
        self._note_event(note, "note_on", due_ns, now, "scheduled")

    def _release(self, reason: str) -> None:
        now = self._clock()
        for note in self._active.values():
            self._note_event(note, "note_off", now, now, reason)
        self._active.clear()
        self._record("silence", at_ns=now, reason=reason)

    def _next_item(self) -> tuple[int, str]:
        lock_tick = self._lock_tick()
        if (
            self._locked_until == self._bar
            and self._continues_to(self._bar + 1)
            and (not self._scheduled or self._scheduled[0][0] >= lock_tick)
        ):
            return self._deadline(lock_tick), "lock"
        if self._scheduled:
            return self._deadline(self._scheduled[0][0]), "note"
        return self._deadline(self._active_settings().bar_ticks), "next_bar"

    def next_deadline_ns(self) -> int | None:
        with self._condition:
            return self._next_item()[0] if self._transport == "playing" else None

    def _pump(self) -> None:
        while self._transport == "playing":
            due, kind = self._next_item()
            now = self._clock()
            if due > now:
                return
            if kind == "lock":
                self._lock_bar(self._bar + 1)
            elif kind == "note":
                _, action, _, note = heapq.heappop(self._scheduled)
                self._dispatch(note, action, due, now)
            elif self._continues_to(self._bar + 1):
                bar_ticks = self._active_settings().bar_ticks
                self._bar_start_tick += bar_ticks
                start = self._bar_start + bar_ticks * self._rate
                if self.phase_aligner is not None:
                    start = self._aligned_start(start)
                self._enter_bar(self._bar + 1, start)
            else:
                self._reset("ended")

    def _aligned_start(self, start: Fraction) -> Fraction:
        assert self.phase_aligner is not None
        try:
            length = self._performing.bar_ticks * self._rate
            corrected = self.phase_aligner(math.ceil(start), math.ceil(length))
        except Exception:  # noqa: BLE001 - an external clock must never stop the timeline
            return start
        shift = corrected - math.ceil(start)
        if shift:
            self._record("phase_correction", shift_ns=shift)
        return start + shift

    def pump(self) -> Event:
        with self._condition:
            self._pump()
            return self._snapshot()

    def _run(self) -> None:
        timer = self._timer
        if timer is not None:
            timer.promote()
        with self._condition:
            while not self._closed:
                self._pump()
                timeout = None
                if self._transport == "playing":
                    due = self._next_item()[0]
                    remaining = due - self._clock()
                    if timer is not None and remaining <= timer.window_ns:
                        # Release the lock so commands proceed during the final, precise wait.
                        self._condition.release()
                        try:
                            timer.wait_until(due)
                        finally:
                            self._condition.acquire()
                        continue
                    margin = timer.window_ns if timer is not None else 0
                    timeout = max(0, remaining - margin) / 1_000_000_000
                self._condition.wait(timeout)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("scheduler is closed")

    def play(self, start_ns: int | None = None) -> Event:
        """Start playing now, or at a later start_ns such as a shared Link downbeat."""
        with self._condition:
            self._check_open()
            if self._transport != "playing":
                self._transport = "playing"
                now = self._clock()
                start = now if start_ns is None else max(now, start_ns)
                self._record("transport", at_ns=now, transport="playing", start_ns=start)
                self._lateness.clear()
                self._enter_bar(self._bar, Fraction(start))
                self._condition.notify()
            return self._snapshot()

    def pause(self) -> Event:
        with self._condition:
            self._check_open()
            if self._transport == "playing":
                self._transport = "paused"
                self._release("pause")
                self._record("transport", transport="paused")
                self._condition.notify()
            return self._snapshot()

    def _reset(self, reason: str) -> None:
        self._release(reason)
        configuration = self._desired()
        self._version_counter += 1
        self._configuration = Configuration(
            configuration.settings,
            configuration.tracks,
            self._version_counter,
            configuration.fallback,
            configuration.program_overrides,
        )
        self._pending.clear()
        self._cues.clear()
        self._improvisations.clear()
        self._transport = "stopped"
        self._bar = 0
        self._performing, self._section, _ = project_song(configuration.settings, 0, 0)
        self._bar_start_tick = 0
        self._scheduled.clear()
        self._drafts.clear()
        self._draft_player.clear()
        self._player_phrase = None
        self._player_version += 1
        self._locked.clear()
        self._locked_until = -1
        self._record("transport", transport="stopped", reason=reason)

    def stop(self) -> Event:
        with self._condition:
            self._check_open()
            if self._transport != "stopped":
                self._reset("stop")
                self._condition.notify()
            return self._snapshot()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._reset("close")
            self._closed = True
            self._condition.notify()
        if self._thread is not None:
            self._thread.join()

    @staticmethod
    def _meter_snapshot(settings: MusicSettings) -> Event:
        numerator, denominator = settings.meter_parts
        return {"numerator": numerator, "denominator": denominator}

    @staticmethod
    def _serialize(configuration: Configuration, *, bar: int = 0) -> Event:
        program = configuration.settings.score_keys_program(bar - configuration.song_origin)
        tracks = {
            key: configuration.tracks[key].model_dump(mode="json")
            for key in active_tracks(configuration.settings.lineup)
        }
        if program is not None and "piano" not in configuration.program_overrides:
            tracks["piano"]["program"] = program
        return {
            "state_version": configuration.version,
            "song_origin": configuration.song_origin if configuration.settings.song else None,
            "settings": configuration.settings.model_dump(mode="json"),
            "tracks": tracks,
        }

    def _tick(self) -> int:
        if self._transport != "playing":
            return 0
        elapsed = max(0, self._clock() - self._bar_start)
        return min(self._active_settings().bar_ticks - 1, elapsed // self._rate)

    def _snapshot(self) -> Event:
        pending = []
        for bar, configuration in sorted(self._pending.items()):
            entry = {"bar": bar, **self._serialize(configuration, bar=bar)}
            if (cue := self._cues.get(bar)) and cue.transition.phase == "arrival":
                entry.update(transition=cue.transition.model_dump(mode="json"), pickup_bar=bar - 1)
            pending.append(entry)
        active_cue = self._cues.get(self._bar)
        active_improv = self._improvisation_for(self._bar)
        pending_improv = next(
            (item for item in self._improvisations if item.start_bar > self._bar),
            None,
        )
        plans = {**self._drafts, **self._locked}
        configuration = self._configuration
        settings = self._active_settings()
        section = None
        if song := configuration.settings.song:
            elapsed = self._bar - configuration.song_origin
            index, section_start, entry = song.section_at(elapsed)
            offset = elapsed % song.total_bars - section_start
            section = {
                "index": index,
                "role": entry.role,
                "start_bar": self._bar - offset,
                "end_bar": self._bar - offset + entry.bars,
                "bar_in_section": offset,
                "total_bars": song.total_bars,
            }
        return {
            "transport": self._transport,
            "current_bar": self._bar,
            "tick": self._tick(),
            "meter": self._meter_snapshot(settings),
            "bpm": settings.bpm,
            "section": section,
            "bar_ticks": settings.bar_ticks,
            "pulse_ticks": settings.pulse_ticks,
            "ticks_per_beat": PPQ * 4 // settings.meter_parts[1],
            "sequence": self._sequence,
            "locked_until_bar": self._locked_until,
            "pending": pending[0] if pending else None,
            "pending_changes": pending,
            "plans": [plans[bar].model_dump(mode="json") for bar in sorted(plans)],
            "improvisation_active": active_improv.snapshot() if active_improv else None,
            "improvisation_pending": pending_improv.snapshot() if pending_improv else None,
            "negotiation": list(self._negotiation),
            "transition": (
                plans[self._bar].transition.model_dump(mode="json")
                if self._bar in plans and plans[self._bar].transition
                else active_cue.transition.model_dump(mode="json")
                if active_cue
                else None
            ),
            "timing": {
                "engine": "native" if self._timer is not None else "python",
                "promoted": bool(self._timer and self._timer.promoted),
                "waiting_for_downbeat": self._transport == "playing"
                and self._clock() < self._bar_start,
                **self._lateness.snapshot(),
            },
            "audio_output": self._audio_output,
            "audio_preferred": self._audio_preferred,
            "audio_fallback": self._audio_fallback,
            "audio_reason": self._audio_reason,
            "audio_active": self._resumable_audio() if self._audio_output == "browser" else [],
            **self._serialize(self._configuration, bar=self._bar),
        }

    def snapshot(self) -> Event:
        with self._condition:
            return self._snapshot()

    def events_after(self, sequence: int) -> list[Event]:
        with self._condition:
            return [event for event in self._events if event["sequence"] > sequence]

    def frame(self, sequence: int) -> tuple[Event, list[Event]]:
        with self._condition:
            return self._snapshot(), [
                event for event in self._events if event["sequence"] > sequence
            ]
