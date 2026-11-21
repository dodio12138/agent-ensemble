"""Session lifecycle, asynchronous look-ahead, and audit output."""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Literal
from uuid import uuid4

from band.audio_routing import AudioRouter, AudioSource, SuperColliderSink
from band.conductor import Conductor, genre_preset
from band.improv_arrangement import apply_improvised_fill, apply_solo
from band.jam import JamDirector
from band.link import LinkBridge
from band.live_midi import LiveMidi, MidiFactory
from band.midi import export_midi
from band.midi_input import MidiInput
from band.music import (
    ConductorPlan,
    Genre,
    MusicSettings,
    NegotiationEvent,
    TimeSignature,
    TrackId,
)
from band.native import FluidOutput
from band.planner import Planner
from band.precise_timer import load as load_precise_timer
from band.remote_agents import RemoteAgent
from band.song import project_song
from band.timing import Scheduler

logger = logging.getLogger(__name__)
CONDUCT_INTERVAL = 0.75


class ConductorBusy(Exception):
    pass


class AuditOutput:
    def __init__(
        self,
        path: Path,
        audio_factory: Callable[[], FluidOutput] | None = None,
        sc_factory: Callable[[], SuperColliderSink] | None = None,
        *,
        prefer_sc: bool = False,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8", buffering=1)
        requested = prefer_sc or bool(sc_factory)
        try:
            fallback = audio_factory() if audio_factory else None
        except (OSError, RuntimeError):
            if not requested:
                self.file.close()
                raise
            logger.exception("Could not prepare FluidSynth, using browser fallback")
            fallback = None
        sc = None
        if sc_factory:
            try:
                sc = sc_factory()
            except (OSError, RuntimeError, ValueError):
                logger.exception("Could not attach SuperCollider, using fallback")
        self.audio = AudioRouter(fallback, sc, prefer_supercollider=requested)
        self.queue: SimpleQueue[dict | None] = SimpleQueue()
        self.thread: threading.Thread | None = None
        self.thread_name = f"output-{path.stem}"

    def start(self, switch: Callable[[AudioSource, str | None], None]) -> None:
        self.thread = threading.Thread(
            target=self._drain,
            args=(switch,),
            name=self.thread_name,
            daemon=True,
        )
        self.thread.start()

    def __call__(self, event: dict) -> None:
        self.queue.put(event)

    def _drain(self, switch: Callable[[AudioSource, str | None], None]) -> None:
        interval = 0.02 if self.audio.supercollider else None
        next_poll = 0.0
        with self.file:
            while True:
                if interval and time.monotonic() >= next_poll:
                    self.audio.poll(switch)
                    next_poll = time.monotonic() + interval
                try:
                    event = self.queue.get(timeout=interval)
                except Empty:
                    continue
                if event is None:
                    return
                try:
                    self.audio.deliver(event, switch)
                except (RuntimeError, OSError, ValueError) as exc:
                    self.file.write(
                        json.dumps(
                            {
                                "type": "audio_error",
                                "time_ns": time.perf_counter_ns(),
                                "sequence_ref": event["sequence"],
                                "message": str(exc),
                            }
                        )
                        + "\n"
                    )
                self.file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def close(self) -> None:
        if self.thread:
            self.queue.put(None)
            self.thread.join()
        else:
            self.file.close()
        self.audio.close()


class Session:
    def __init__(
        self,
        state_dir: Path,
        settings: MusicSettings | None = None,
        *,
        planner: Planner | None = None,
        audio_factory: Callable[[], FluidOutput] | None = None,
        conductor: Conductor | None = None,
        midi_factory: MidiFactory = MidiInput,
        sc_factory: Callable[[], SuperColliderSink] | None = None,
        prefer_sc: bool = False,
        precise_timer: bool = True,
    ):
        self.id = uuid4().hex
        self.planner = planner or Planner()
        self.output = AuditOutput(
            state_dir / "sessions" / f"{self.id}.jsonl",
            audio_factory,
            sc_factory,
            prefer_sc=prefer_sc,
        )
        router = self.output.audio
        try:
            self.scheduler = Scheduler(
                self.planner,
                settings,
                output=self.output,
                audio_fallback=router.fallback_source,
                audio_preferred=router.preferred,
                audio_reason=router.status[1],
                timer=load_precise_timer() if precise_timer else None,
            )
        except Exception:
            self.output.close()
            raise
        self.output.start(self.scheduler.switch_audio)
        self.coordinator: asyncio.Task | None = None
        self.commands = asyncio.Lock()
        self.planning = asyncio.Lock()
        self.conductor = conductor or Conductor()
        self.conducting = asyncio.Lock()
        self.last_conduct_start = float("-inf")
        self.last_conductor_result: dict | None = None
        self.composition_task: asyncio.Task | None = None
        self.refinement_task: asyncio.Task | None = None
        self.refinement: dict | None = None
        self.composition_source: str | None = None
        self.composition_error: str | None = None
        self.improvisation_auto = False
        self.improvisation_task: asyncio.Task | None = None
        self.improvisation_is_automatic = False
        self.improvisation_variant = 0
        self.improvisation_error: str | None = None
        self.improvisation_retry_at = 0.0
        self.midi = LiveMidi(self.scheduler, midi_factory)
        self.link = LinkBridge(self.scheduler, self._link_transport)
        self.jam = JamDirector(self.scheduler, seed=int(self.id[:8], 16))
        self.remote_agents: dict[TrackId, RemoteAgent] = {}
        self.viewers = 0
        self._link_task: asyncio.Task | None = None

    async def transport(self, action: Literal["play", "pause", "stop"]) -> dict:
        async with self.commands:
            if action == "play":
                await self.plan_ahead()
                self.scheduler.play(self.link.start_time())
            elif action == "pause":
                self.scheduler.pause()
                self.midi.clear_phrase()
            else:
                self.cancel_improvisation()
                self.scheduler.stop()
                self.jam.reset()
                self.midi.clear_phrase()
                await self.plan_ahead()
            self.link.notify_transport(action == "play")
            return self.snapshot()

    def configure_jam(self, *, enabled: bool | None = None, profile: str | None = None) -> None:
        if enabled:
            # The jam director plans its own solos; the chorus-level auto mode would clash.
            self.set_automatic_improvisation(False)
        self.jam.configure(enabled=enabled, profile=profile)

    def _link_transport(self, action: Literal["play", "pause"]) -> None:
        if self._link_task is None or self._link_task.done():
            self._link_task = asyncio.create_task(self.transport(action), name=f"link-{self.id}")

    def attach_remote(self, agent: RemoteAgent) -> RemoteAgent | None:
        """Hand a track to a remote musician; returns the agent it replaced, if any."""
        previous = self.remote_agents.get(agent.track)
        self.remote_agents[agent.track] = agent
        self.planner.attach(agent.track, agent)
        self.scheduler.record_remote_agent(agent.track, "connected", name=agent.name)
        return previous

    def detach_remote(self, agent: RemoteAgent) -> None:
        agent.disconnect()
        self.planner.detach(agent.track, agent)
        if self.remote_agents.get(agent.track) is agent:
            del self.remote_agents[agent.track]
            self.scheduler.record_remote_agent(agent.track, "disconnected", name=agent.name)

    def snapshot(self) -> dict:
        return self.format_state(self.scheduler.snapshot())

    def format_state(self, state: dict) -> dict:
        result = self.last_conductor_result
        if (
            result
            and result["status"] == "scheduled"
            and state["state_version"] >= result["state_version"]
        ):
            progression = result.get("progression")
            matches = (
                state["settings"]["conductor"] == result["plan"]
                and (progression is None or state["settings"]["progression"] == progression)
                and all(
                    result.get(key) is None or state["settings"][key] == result[key]
                    for key in ("bpm", "meter")
                )
            )
            if matches:
                result = {**result, "status": "applied"}
                if (
                    state["transport"] == "stopped"
                    and state["current_bar"] < result["effective_bar"]
                ):
                    result["effective_bar"] = 0
            else:
                result = {**result, "status": "unchanged", "reason": "superseded"}
            self.last_conductor_result = result
        return {
            **state,
            "id": self.id,
            "bar": state["current_bar"],
            "conductor": result,
            "conductor_model": self.conductor.model,
            "refinement": self.refinement,
            "composition": {
                "planning": self.composition_task is not None and not self.composition_task.done(),
                "source": self.composition_source,
                "error": self.composition_error,
                "start_bar": next(
                    (
                        change["song_origin"]
                        for change in reversed(state["pending_changes"])
                        if change["settings"].get("song")
                    ),
                    state["song_origin"],
                ),
            },
            "improvisation": {
                "auto": self.improvisation_auto,
                "planning": self.improvisation_planning,
                "active": state["improvisation_active"],
                "pending": state["improvisation_pending"],
                "last_error": self.improvisation_error,
            },
            "midi": self.midi.snapshot(),
            "link": self.link.snapshot(),
            "jam": self.jam.snapshot(),
            "remote_agents": {
                track: agent.snapshot() for track, agent in self.remote_agents.items()
            },
        }

    async def prime(self) -> None:
        self.midi.start()
        await self.plan_ahead()
        self.coordinator = asyncio.create_task(self._coordinate(), name=f"planner-{self.id}")

    async def _coordinate(self) -> None:
        while True:
            self.midi.update_phrase()
            try:
                self.link.poll()
            except Exception:
                logger.exception("Ableton Link poll failed for session %s", self.id)
            try:
                self.jam.poll()
            except Exception:
                logger.exception("Jam director failed for session %s", self.id)
            await self.plan_ahead()
            if (
                self.improvisation_auto
                and not self.improvisation_planning
                and time.monotonic() >= self.improvisation_retry_at
                and self.scheduler.wants_improvisation()
            ):
                self.start_improvisation("", automatic=True)
            await asyncio.sleep(0.035)

    async def plan_ahead(self) -> None:
        async with self.planning:
            targets = self.scheduler.planning_targets()
            if targets:
                await asyncio.gather(*(self._plan_target(*target) for target in targets))

    async def _plan_target(self, bar: int, settings: MusicSettings, version: int) -> None:
        phrase, player_version = self.scheduler.player_context()
        settings, variation, band_phrase, connection = self.scheduler.arrangement_for(bar, settings)

        def tag(event: NegotiationEvent) -> NegotiationEvent:
            return (
                event.model_copy(
                    update={
                        "proposal_id": f"{event.proposal_id}:p{player_version}:v{variation or 0}",
                    }
                )
                if player_version or variation
                else event
            )

        def publish(event: NegotiationEvent) -> None:
            self.scheduler.record_negotiation(bar, version, tag(event))

        try:
            plan = await self.planner.plan(
                bar,
                settings,
                version,
                player_phrase=phrase,
                on_negotiation=publish,
                **connection,
            )
            if (player_version or variation) and plan.negotiation:
                plan = plan.model_copy(update={"negotiation": tuple(map(tag, plan.negotiation))})
            if band_phrase:
                offset = self.scheduler.improvisation_offset(bar)
                plan = apply_improvised_fill(
                    plan, settings, band_phrase, half=None if offset is None else offset % 2
                )
            if solo := self.jam.solo_for(bar):
                plan = apply_solo(plan, settings, *solo)
            plan = plan.model_copy(
                update={
                    "harmony": settings.chord_at(bar).symbol,
                    "bpm": settings.bpm,
                    "time_signature": settings.meter,
                    "improvisation_id": variation,
                    "improvisation_phrase": band_phrase,
                }
            )
            self.scheduler.commit(plan, player_version=player_version)
        except Exception:
            logger.exception("Planning failed for session %s, bar %s", self.id, bar)

    async def conduct(self, instruction: str) -> dict:
        if self.conducting.locked():
            raise ConductorBusy
        async with self.conducting:
            started = time.perf_counter()
            if started - self.last_conduct_start < CONDUCT_INTERVAL:
                raise ConductorBusy
            self.last_conduct_start = started
            settings, _ = self.scheduler.desired_configuration()
            previous = settings.conductor
            proposal = (
                await self.conductor.propose(instruction, previous)
                if settings.song
                else await self.conductor.propose_with_timing(instruction, settings)
            )
            async with self.commands:
                latest, _ = self.scheduler.desired_configuration()
                if latest.conductor != previous or any(
                    getattr(proposal, key) is not None
                    and getattr(latest, key) != getattr(settings, key)
                    for key in ("progression", "bpm", "meter")
                ):
                    result = {
                        **proposal.model_dump(mode="json"),
                        "status": "unchanged",
                        "reason": "superseded",
                    }
                    self.last_conductor_result = result
                    self.scheduler.record_conductor(result, instruction=instruction)
                    return self.snapshot()
                return await self._apply_conductor(
                    proposal.plan,
                    proposal.model_dump(mode="json"),
                    instruction,
                    started,
                    progression=proposal.progression,
                    bpm=proposal.bpm,
                    meter=proposal.meter,
                    next_chorus=proposal.timing_boundary == "next_chorus",
                )

    async def preset(self, style: Genre, *, refine: bool = True) -> dict:
        """Apply the rule preset now, then let the model arrange the style in the background."""
        started = time.perf_counter()
        async with self.commands:
            current, _ = self.scheduler.desired_configuration()
            plan, progression = genre_preset(style, current.conductor)
            state = await self._apply_conductor(
                plan,
                {
                    "source": "preset",
                    "model": None,
                    "reason": None,
                    "attempts": 0,
                    "prompt_version": "style-preset-v1",
                },
                f"preset:{style}",
                started,
                progression=progression,
            )
        if refine:
            self.start_refinement(style)
        return self.snapshot() if refine else state

    def start_refinement(self, genre: Genre) -> None:
        if self.refinement_task is not None and not self.refinement_task.done():
            self.refinement_task.cancel()
        settings, _ = self.scheduler.desired_configuration()
        if not self.conductor.model:
            self.refinement = {"genre": genre, "status": "skipped", "reason": "not_configured"}
            return
        if settings.song is not None or self.jam.enabled:
            reason = "song" if settings.song is not None else "jam"
            self.refinement = {"genre": genre, "status": "skipped", "reason": reason}
            return
        self.refinement = {"genre": genre, "status": "planning"}
        self.refinement_task = asyncio.create_task(self._refine(genre), name=f"refine-{self.id}")

    async def _refine(self, genre: Genre) -> None:
        started = time.perf_counter()
        settings, _ = self.scheduler.desired_configuration()
        try:
            arrangement, attempt = await self.conductor.arrange_genre(genre, settings)
        except Exception:
            logger.exception("Style refinement failed for session %s", self.id)
            self.refinement = {"genre": genre, "status": "failed", "reason": "unavailable"}
            return
        if arrangement is None:
            self.refinement = {"genre": genre, "status": "failed", "reason": attempt.reason}
            return
        async with self.commands:
            latest, _ = self.scheduler.desired_configuration()
            if (
                latest.conductor.profile.genre
                if latest.conductor.profile
                else latest.conductor.style
            ) != genre:
                self.refinement = {"genre": genre, "status": "superseded"}
                return
            if arrangement.density != latest.density:
                self.scheduler.stage_settings({"density": arrangement.density})
            for track, patch in arrangement.tracks.items():
                self.scheduler.stage_track(track, patch)
            state = await self._apply_conductor(
                arrangement.plan,
                {
                    "source": "llm",
                    "model": attempt.model,
                    "reason": None,
                    "attempts": attempt.attempts,
                    "prompt_version": "genre-arrangement-v1",
                    "note": arrangement.note,
                    "corrections": list(arrangement.corrections),
                },
                f"arrange:{genre}",
                started,
                progression=arrangement.progression,
                bpm=arrangement.bpm,
            )
        result = state.get("conductor") or {}
        self.refinement = {
            "genre": genre,
            "status": "applied",
            "note": arrangement.note,
            "bpm": arrangement.bpm,
            "effective_bar": result.get("effective_bar"),
            "corrections": list(arrangement.corrections),
            "tones": {track: patch["program"] for track, patch in arrangement.tracks.items()},
        }

    async def _apply_conductor(
        self,
        plan: ConductorPlan,
        metadata: dict,
        instruction: str,
        started: float,
        *,
        progression: tuple[str, ...] | None = None,
        bpm: int | None = None,
        meter: TimeSignature | None = None,
        next_chorus: bool = False,
    ) -> dict:
        settings, _ = self.scheduler.desired_configuration()
        previous = settings.conductor
        status, bar, version = "unchanged", None, None
        patch = {}
        if plan != previous:
            patch["conductor"] = plan.model_dump()
        if progression is not None and progression != settings.progression:
            patch["progression"] = progression
            patch["conductor"] = plan.model_dump()
        if bpm is not None and bpm != settings.bpm:
            patch["bpm"] = bpm
        if meter is not None and meter != settings.meter:
            patch["meter"] = meter
        if patch:
            state = self.scheduler.stage_settings(
                patch, next_chorus=next_chorus and ("bpm" in patch or "meter" in patch)
            )
            if pending := state["pending_changes"]:
                status, change = "scheduled", pending[-1]
                bar, version = change["bar"], change["state_version"]
            else:
                status, bar, version = "applied", state["current_bar"], state["state_version"]
        result = {
            **metadata,
            "plan": plan.model_dump(mode="json"),
            "status": status,
            "effective_bar": bar,
            "state_version": version,
        }
        self.last_conductor_result = result
        self.scheduler.record_conductor(
            result,
            instruction=instruction,
            previous_plan=previous.model_dump(mode="json"),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        if status != "unchanged":
            await self.plan_ahead()
        return self.snapshot()

    @property
    def improvisation_planning(self) -> bool:
        return self.improvisation_task is not None and not self.improvisation_task.done()

    def start_composition(self, instruction: str) -> None:
        if self.composition_task is not None and not self.composition_task.done():
            raise ConductorBusy
        self.composition_error = None
        self.composition_task = asyncio.create_task(
            self._compose(instruction), name=f"compose-{self.id}"
        )

    async def _compose(self, instruction: str) -> None:
        settings, _ = self.scheduler.desired_configuration()
        try:
            result = await self.conductor.compose(instruction, settings)
            async with self.commands:
                self.scheduler.stage_settings(
                    {"song": result.song.model_dump(), "conductor": result.plan.model_dump()}
                )
                self.composition_source = result.source
                self.scheduler.record_conductor(
                    {
                        "status": "composition",
                        "source": result.source,
                        "reason": result.reason,
                        "total_bars": result.song.total_bars,
                    },
                    instruction=instruction,
                )
                await self.plan_ahead()
        except Exception:
            logger.exception("Composition failed for session %s", self.id)
            self.composition_error = "全曲编排未能生成"

    async def clear_composition(self) -> dict:
        if self.composition_task is not None and not self.composition_task.done():
            self.composition_task.cancel()
        self.composition_error = None
        async with self.commands:
            self.scheduler.stage_settings({"song": None})
            await self.plan_ahead()
        return self.snapshot()

    def start_improvisation(self, instruction: str, *, automatic: bool = False) -> None:
        if self.improvisation_planning:
            raise ConductorBusy
        self.improvisation_error = None
        self.improvisation_is_automatic = automatic
        self.improvisation_variant += 1
        self.improvisation_task = asyncio.create_task(
            self._plan_improvisation(instruction, self.improvisation_variant, automatic),
            name=f"improvise-{self.id}",
        )
        self.scheduler.record_improvisation("planning", automatic=automatic)

    async def _plan_improvisation(self, instruction: str, variant: int, automatic: bool) -> None:
        theme = self.scheduler.improvisation_theme()
        try:
            result = await self.conductor.improvise(instruction, theme, variant)
            async with self.commands:
                if automatic and self.scheduler.position()[0] == "stopped":
                    return
                metadata = {
                    "source": result.source,
                    "model": result.model,
                    "reason": result.reason,
                    "prompt_version": result.prompt_version,
                    "attempts": result.attempts,
                    "automatic": automatic,
                }
                chorus = self.scheduler.schedule_improvisation(
                    result.plan, variant, theme, metadata
                )
                if chorus is None:
                    self.improvisation_error = "主题或播放位置已改变，请重新发起即兴"
                else:
                    self.scheduler.record_improvisation("scheduled", **chorus.snapshot())
                    await self.plan_ahead()
        except Exception:
            logger.exception("Could not prepare the improvisation in session %s", self.id)
            self.improvisation_error = "这轮即兴未排入演奏"
        finally:
            self.improvisation_retry_at = time.monotonic() + 2
            self.scheduler.record_improvisation("finished", error=self.improvisation_error)

    def set_automatic_improvisation(self, enabled: bool) -> None:
        self.improvisation_auto = enabled
        if not enabled:
            if self.improvisation_is_automatic:
                self.cancel_improvisation()
            self.scheduler.cancel_queued_automatic_improvisation()
        self.scheduler.record_improvisation("auto", automatic=enabled)

    def cancel_improvisation(self) -> None:
        if self.improvisation_task and not self.improvisation_task.done():
            self.improvisation_task.cancel()

    async def export(self) -> bytes:
        settings, tracks, follow_score_tone = self.scheduler.export_context()
        arranger = Planner()
        if settings.song is None:
            plans = [
                await arranger.plan(bar, settings, 0) for bar in range(len(settings.progression))
            ]
        else:
            plans = []
            for bar in range(settings.song.total_bars):
                projected, section, offset = project_song(settings, 0, bar)
                previous = project_song(settings, 0, bar - 1)[0]
                following = project_song(settings, 0, bar + 1)[0]
                plan = await arranger.plan(
                    bar,
                    projected,
                    0,
                    section=section,
                    section_bar=offset,
                    voice_leading=True,
                    prior_chord=previous.chord_at(bar - 1) if bar else None,
                    next_chord=following.chord_at(bar + 1)
                    if bar + 1 < settings.song.total_bars
                    else None,
                )
                plans.append(
                    plan.model_copy(
                        update={"bpm": projected.bpm, "time_signature": projected.meter}
                    )
                )
        return export_midi(settings, tracks, plans, use_score_tone=follow_score_tone)

    async def close(self) -> None:
        if self.composition_task is not None and not self.composition_task.done():
            self.composition_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.composition_task
        if self.refinement_task is not None and not self.refinement_task.done():
            self.refinement_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.refinement_task
        self.cancel_improvisation()
        if self.improvisation_task is not None:
            with suppress(asyncio.CancelledError):
                await self.improvisation_task
        if self.coordinator:
            self.coordinator.cancel()
            with suppress(asyncio.CancelledError):
                await self.coordinator
        await self.midi.close()
        self.link.close()
        for agent in list(self.remote_agents.values()):
            self.detach_remote(agent)
        self.scheduler.close()
        await asyncio.to_thread(self.output.close)
