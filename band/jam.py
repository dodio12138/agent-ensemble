"""A jam director: the band keeps reshaping the tune while it plays.

Every eight bars the director picks the next move along an energy arc: rotating solos,
builds and peaks, breakdowns and stops, tempo pushes, groove switches and a key change
at the top, then back to the theme. Each move becomes ordinary staged settings and track
changes, so the scheduler's bar locking, transitions and fallbacks apply unchanged.
Solo bars are painted onto the band's plan by ``improv_arrangement.apply_solo``.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from band.conductor import genre_preset
from band.music import CHORUS_BARS, ConductorPlan, Genre, TrackId, active_tracks

if TYPE_CHECKING:
    from band.timing import Scheduler

Move = Literal["theme", "solo", "build", "peak", "push", "groove", "breakdown", "stop", "cooldown"]
MOVE_NAMES: dict[Move, str] = {
    "theme": "主题",
    "solo": "独奏",
    "build": "推进",
    "peak": "高潮",
    "push": "加速",
    "groove": "换律动",
    "breakdown": "降温·留白",
    "stop": "急停再进",
    "cooldown": "收",
}
TRACK_NAMES: dict[TrackId, str] = {
    "drums": "鼓",
    "bass": "贝斯",
    "piano": "键盘",
    "rhythm_guitar": "节奏吉他",
    "lead_guitar": "主奏吉他",
    "synth": "手风琴",
}
GENRE_NAMES: dict[str, str] = {
    "disco": "迪斯科",
    "reggae": "雷鬼",
    "funk": "放克",
    "half_time": "半拍律动",
    "swing": "摇摆",
    "rock": "摇滚",
}
# One arc: theme, a solo, a colour change, build, peak, then come down. Slots holding a
# tuple rotate through it, so successive arcs differ while each still climbs and lands.
ARC: tuple[Move | tuple[Move, ...], ...] = (
    "theme",
    "solo",
    ("groove", "push", "solo"),
    "build",
    "peak",
    ("breakdown", "stop", "cooldown"),
)
_SOLO_CHARACTERS = (("answer", "answer", "build", "resolve"), ("space", "answer", "build", "build"))


@dataclass(frozen=True)
class JamProfile:
    name: str
    label: str
    home: dict[str, Any] | None
    tracks: dict[TrackId, dict[str, Any]]
    soloists: tuple[TrackId, ...]
    grooves: tuple[str, ...]


PROFILES = {
    "current": JamProfile(
        "current",
        "沿用当前编排",
        None,
        {},
        ("lead_guitar", "piano", "bass", "drums"),
        ("half_time", "funk", "reggae"),
    ),
    "wutiaoren": JamProfile(
        "wutiaoren",
        "五条人式民谣摇滚",
        {
            "lineup": "modern",
            "genre": "rock",
            "bpm": 120,
            "density": 0.6,
            "progression": ("C", "C", "F", "F", "G", "G", "C", "G"),
            "intensity": 62,
        },
        {
            "rhythm_guitar": {"program": 29, "volume": 0.7, "muted": False},
            "lead_guitar": {"program": 27, "volume": 0.66, "muted": False},
            "synth": {"program": 21, "volume": 0.5, "muted": False},
            "piano": {"program": 4, "volume": 0.55, "muted": False},
            "bass": {"program": 34, "volume": 0.8, "muted": False},
            "drums": {"volume": 0.82, "muted": False},
        },
        ("lead_guitar", "synth", "piano", "bass", "drums"),
        ("disco", "reggae", "funk", "half_time"),
    ),
}

_NOTE = re.compile(r"([A-G])([#b]?)")
_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_FLATS = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_SHARPS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def transpose(symbol: str, steps: int) -> str:
    """Move a chord symbol's root and slash bass by semitones."""

    def shift(match: re.Match[str]) -> str:
        pitch = _PITCH[match.group(1)] + {"#": 1, "b": -1, "": 0}[match.group(2)]
        # Keep sharp spellings sharp; otherwise use the common flat names.
        return (_SHARPS if match.group(2) == "#" else _FLATS)[(pitch + steps) % 12]

    root, _, bass = symbol.partition("/")
    moved = _NOTE.sub(shift, root, count=1)
    return f"{moved}/{_NOTE.sub(shift, bass, count=1)}" if bass else moved


@dataclass
class Section:
    start: int
    move: Move
    label: str
    early: dict[str, Any] = field(default_factory=dict)  # style / tempo / harmony
    late: dict[str, Any] = field(default_factory=dict)  # density
    tracks: dict[TrackId, dict[str, Any]] = field(default_factory=dict)
    cues: dict[int, dict[TrackId, dict[str, Any]]] = field(default_factory=dict)
    solo: TrackId | None = None
    characters: tuple[str, ...] = ()
    staged: set[str] = field(default_factory=set)

    @property
    def end(self) -> int:
        return self.start + CHORUS_BARS

    def snapshot(self) -> dict[str, Any]:
        return {
            "start_bar": self.start,
            "end_bar": self.end,
            "move": self.move,
            "label": self.label,
        }


class JamDirector:
    def __init__(self, scheduler: Scheduler, seed: int = 0) -> None:
        self._scheduler = scheduler
        self._seed = seed
        self.enabled = False
        self.profile = PROFILES["current"]
        self._home: dict[str, Any] = {}
        self._home_tracks: dict[TrackId, dict[str, Any]] = {}
        self._sections: list[Section] = []
        self._rng = random.Random(seed)
        self._planned = 0
        self._tempo = 120
        self._soloist = 0
        self._groove = 0
        self.history: list[dict[str, Any]] = []

    # -- control -----------------------------------------------------------------

    def configure(self, *, enabled: bool | None = None, profile: str | None = None) -> None:
        if profile is not None:
            if profile not in PROFILES:
                raise ValueError(f"Unknown jam profile: {profile}")
            self.profile = PROFILES[profile]
        if enabled is True and not self.enabled:
            self._start()
        elif enabled is False and self.enabled:
            self._stop()
        elif enabled is None and profile is not None and self.enabled:
            self._stop()
            self._start()

    def _start(self) -> None:
        self.enabled = True
        self._rng = random.Random(self._seed)
        self._sections.clear()
        self.history.clear()
        self._planned, self._soloist, self._groove = 0, 0, 0
        if home := self.profile.home:
            plan, _ = genre_preset(home["genre"], ConductorPlan())
            plan = plan.model_copy(update={"intensity": home["intensity"]})
            self._scheduler.stage_settings(
                {
                    "lineup": home["lineup"],
                    "bpm": home["bpm"],
                    "density": home["density"],
                    "progression": home["progression"],
                    "conductor": plan.model_dump(),
                }
            )
            for track, patch in self.profile.tracks.items():
                self._scheduler.stage_track(track, patch)
        settings, tracks = self._scheduler.desired_configuration()
        self._home = {
            "bpm": settings.bpm,
            "density": settings.density,
            "progression": settings.progression,
            "conductor": settings.conductor,
            "lineup": settings.lineup,
        }
        self._home_tracks = {
            track: {"volume": value.volume, "muted": value.muted} for track, value in tracks.items()
        }
        self._tempo = settings.bpm

    def _stop(self) -> None:
        self.enabled = False
        upcoming = [section for section in self._sections if section.staged]
        self._sections.clear()
        if upcoming or self.history:
            self._scheduler.stage_settings(self._home_patch())
            for track, patch in self._home_tracks.items():
                self._scheduler.stage_track(track, patch)

    def reset(self) -> None:
        """Transport stopped: the scheduler dropped pending changes; plan afresh."""
        self._sections.clear()
        self._planned = 0

    # -- planning ----------------------------------------------------------------

    def poll(self) -> None:
        """Plan ahead and stage what is due. Runs on the session's event loop."""
        if not self.enabled:
            return
        transport, bar, _ = self._scheduler.position()
        if transport != "playing":
            return
        self._sections = [section for section in self._sections if section.end > bar]
        if not self._sections:
            first = -(-(bar + 3) // CHORUS_BARS) * CHORUS_BARS
            self._sections.append(self._compose("theme", first))
        while self._sections[-1].start <= bar + 3:
            self._sections.append(self._compose(self._choose(), self._sections[-1].end))
        for section in self._sections:
            if "early" not in section.staged and bar >= section.start - 2:
                section.staged.add("early")
                if section.early:
                    self._scheduler.stage_settings(section.early)
                self._log(section)
            if "late" not in section.staged and bar >= section.start - 1:
                section.staged.add("late")
                if section.late:
                    self._scheduler.stage_settings(section.late)
                for track, patch in section.tracks.items():
                    self._scheduler.stage_track(track, patch)
            for offset, tracks in section.cues.items():
                key = f"cue{offset}"
                if key not in section.staged and bar >= section.start + offset - 1:
                    section.staged.add(key)
                    for track, patch in tracks.items():
                        self._scheduler.stage_track(track, patch)

    def _choose(self) -> Move:
        slot = ARC[self._planned % len(ARC)]
        if isinstance(slot, tuple):
            arc = self._planned // len(ARC)
            slot = slot[(arc + self._rng.randrange(2)) % len(slot)] if arc else slot[0]
        return slot

    def _home_patch(self) -> dict[str, Any]:
        return {
            "bpm": self._home["bpm"],
            "density": self._home["density"],
            "progression": self._home["progression"],
            "conductor": self._home["conductor"].model_dump(),
        }

    def _plan(self, **updates: Any) -> dict[str, Any]:
        conductor: ConductorPlan = self._home["conductor"]
        return conductor.model_copy(update=updates).model_dump()

    def _volumes(self, factor: float, *, boost: TrackId | None = None) -> dict:
        tracks = {}
        for track in active_tracks(self._home["lineup"]):
            home = self._home_tracks.get(track, {"volume": 0.8, "muted": False})
            level = home["volume"] * (1.15 if track == boost else factor)
            tracks[track] = {"volume": round(min(1.0, max(0.05, level)), 2), "muted": home["muted"]}
        return tracks

    def _compose(self, move: Move, start: int) -> Section:
        home = self._home
        conductor: ConductorPlan = home["conductor"]
        intensity = conductor.intensity
        density = home["density"]
        section = Section(start, move, MOVE_NAMES[move])
        if move == "theme":
            self._tempo = home["bpm"]
            section.early = self._home_patch()
            section.tracks = self._volumes(1.0)
        elif move == "solo":
            soloists = [
                track for track in self.profile.soloists if track in active_tracks(home["lineup"])
            ]
            soloist = soloists[self._soloist % len(soloists)]
            self._soloist += 1
            section.solo = soloist
            section.characters = self._rng.choice(_SOLO_CHARACTERS)
            section.label = f"{TRACK_NAMES[soloist]}独奏"
            section.late = {"density": round(max(0.3, density - 0.1), 2)}
            section.tracks = self._volumes(0.82, boost=soloist)
        elif move == "build":
            self._tempo = min(home["bpm"] + 20, self._tempo + 4)
            section.early = {
                "bpm": self._tempo,
                "conductor": self._plan(intensity=min(95, intensity + 15), drums="driving"),
            }
            section.late = {"density": round(min(0.9, density + 0.2), 2)}
            section.tracks = self._volumes(1.08)
        elif move == "peak":
            # Push into the climax: a little faster and a whole step higher.
            self._tempo = min(home["bpm"] + 20, self._tempo + 8)
            up = tuple(transpose(chord, 2) for chord in home["progression"])
            section.early = {
                "bpm": self._tempo,
                "progression": up,
                "conductor": self._plan(intensity=min(100, intensity + 28), drums="driving"),
            }
            section.late = {"density": 0.9}
            section.tracks = self._volumes(1.15)
            section.label = f"高潮·升全音·{self._tempo} BPM"
        elif move == "push":
            self._tempo = min(home["bpm"] + 20, self._tempo + 10)
            section.early = {"bpm": self._tempo}
            section.late = {"density": round(min(0.85, density + 0.15), 2)}
            section.label = f"加速到 {self._tempo} BPM"
        elif move == "groove":
            name = self.profile.grooves[self._groove % len(self.profile.grooves)]
            self._groove += 1
            if name == "half_time" and conductor.profile is not None:
                plan = conductor.model_copy(
                    update={"profile": conductor.profile.model_copy(update={"groove": "half_time"})}
                )
            else:
                genre: Genre = "funk" if name == "half_time" else name  # type: ignore[assignment]
                plan, _ = genre_preset(genre, conductor)
                plan = plan.model_copy(update={"intensity": intensity})
            section.early = {"conductor": plan.model_dump(), "progression": home["progression"]}
            section.label = f"换成{GENRE_NAMES.get(name, name)}"
        elif move == "breakdown":
            self._tempo = home["bpm"]
            section.early = {
                "bpm": self._tempo,
                "conductor": self._plan(intensity=max(25, intensity - 20), drums="sparse"),
            }
            section.late = {"density": 0.3}
            dropped = {"rhythm_guitar", "synth", "lead_guitar"}
            section.tracks = {
                track: {"muted": True}
                for track in dropped
                if track in active_tracks(home["lineup"])
            }
            section.cues = {
                4: {
                    track: {"muted": False}
                    for track in ("rhythm_guitar", "synth")
                    if track in active_tracks(home["lineup"])
                }
            }
        elif move == "stop":
            section.tracks = {
                track: {"muted": True}
                for track in active_tracks(home["lineup"])
                if track != "drums"
            }
            section.cues = {2: self._volumes(1.1)}
            section.label = "急停·鼓独留两小节"
        else:  # cooldown
            self._tempo = max(home["bpm"] - 12, self._tempo - 10)
            section.early = {
                "bpm": self._tempo,
                "conductor": self._plan(intensity=max(25, intensity - 15)),
            }
            section.late = {"density": round(max(0.3, density - 0.2), 2)}
            section.tracks = self._volumes(0.85)
        self._planned += 1
        return section

    def _log(self, section: Section) -> None:
        entry = section.snapshot()
        self.history = [*self.history[-7:], entry]
        self._scheduler.record_jam(section.move, label=section.label, start_bar=section.start)

    # -- performance -------------------------------------------------------------

    def solo_for(self, bar: int) -> tuple[TrackId, str, int] | None:
        """The soloist, cue character and half (call / answer) for a bar, if any."""
        for section in self._sections:
            if section.solo and section.start <= bar < section.end:
                offset = bar - section.start
                return section.solo, section.characters[offset // 2], offset % 2
        return None

    def snapshot(self) -> dict[str, Any]:
        transport, bar, _ = self._scheduler.position()
        current = next(
            (section for section in self._sections if section.start <= bar < section.end), None
        )
        upcoming = next((section for section in self._sections if section.start > bar), None)
        return {
            "enabled": self.enabled,
            "profile": self.profile.name,
            "profiles": {name: profile.label for name, profile in PROFILES.items()},
            "current": current.snapshot() if current and transport == "playing" else None,
            "next": upcoming.snapshot() if upcoming and self.enabled else None,
            "history": self.history[-5:],
        }
