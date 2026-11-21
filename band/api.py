"""Local HTTP controls and a sequenced WebSocket event feed."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from band.audio_routing import SuperColliderSink
from band.conductor import Conductor
from band.live_midi import MidiFactory
from band.midi_input import MidiInput
from band.music import (
    TRACK_DEFINITIONS,
    Collaboration,
    Genre,
    Lineup,
    MusicSettings,
    TimeSignature,
    TrackId,
)
from band.native import FluidOutput
from band.planner import Planner
from band.remote_agents import PROTOCOL, REMOTE_DEADLINE, RemoteAgent
from band.session import ConductorBusy, Session

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bpm: int | None = None
    density: float | None = None
    progression: tuple[str, ...] | None = None
    loop: bool | None = None
    seed: int | None = None
    collaboration: Collaboration | None = None
    lineup: Lineup | None = None
    meter: TimeSignature | None = None


class TrackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume: float | None = None
    muted: bool | None = None
    program: int | None = None


class ConductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(min_length=1, max_length=500)


class PresetRequest(BaseModel):
    style: Genre


class ImprovisationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(default="", max_length=500)


class AutomaticImprovisationRequest(BaseModel):
    auto: bool


class LinkPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    start_stop: bool | None = None


class RemoteHello(BaseModel):
    type: Literal["hello"]
    name: str = Field(default="远程乐手", min_length=1, max_length=40)
    protocol: str = PROTOCOL
    deadline_ms: int = Field(default=round(REMOTE_DEADLINE * 1000), ge=40, le=1000)


class MidiPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: str | None = Field(default=None, min_length=1, max_length=128)
    volume: float | None = Field(default=None, ge=0, le=1)
    program: int | None = Field(default=None, ge=0, le=127)
    adaptive: bool | None = None


def create_app(
    state_dir: Path = ROOT / ".runtime" / "data",
    frontend: Path = ROOT / "frontend" / "dist",
    *,
    planner_factory: Callable[[], Planner] = Planner,
    audio_factory: Callable[[], FluidOutput] | None = None,
    conductor: Conductor | None = None,
    midi_factory: MidiFactory = MidiInput,
    sc_factory: Callable[[], SuperColliderSink] | None = None,
    prefer_sc: bool = False,
) -> FastAPI:
    sessions: dict[str, Session] = {}
    created: dict[str, float] = {}
    conductor = conductor or Conductor()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await asyncio.gather(*(session.close() for session in sessions.values()))
        sessions.clear()

    app = FastAPI(
        title="Agent Ensemble",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.middleware("http")
    async def verify_control_origin(request: Request, call_next: Callable) -> Response:
        origin = request.headers.get("origin")
        if (
            request.method in {"POST", "PATCH"}
            and origin
            and origin != str(request.base_url).rstrip("/")
        ):
            return Response(status_code=403)
        return await call_next(request)

    def get_session(session_id: str) -> Session:
        try:
            return sessions[session_id]
        except KeyError:
            raise HTTPException(404, "Session not found") from None

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/sessions", status_code=201)
    async def create_session(config: MusicSettings | None = None) -> dict:
        try:
            session = Session(
                state_dir,
                config,
                planner=planner_factory(),
                audio_factory=audio_factory,
                conductor=conductor,
                midi_factory=midi_factory,
                sc_factory=sc_factory,
                prefer_sc=prefer_sc,
            )
        except (OSError, RuntimeError) as exc:
            logger.exception("Could not create local session")
            raise HTTPException(503, str(exc)) from exc
        sessions[session.id] = session
        created[session.id] = time.time()
        await session.prime()
        return session.snapshot()

    @app.get("/sessions")
    def list_sessions() -> list[dict]:
        """Sessions with an open console first, then newest first, for remote agents."""
        order = sorted(
            sessions,
            key=lambda key: (sessions[key].viewers == 0, -created.get(key, 0)),
        )
        return [
            {
                "id": session_id,
                "created_at": created.get(session_id),
                "viewers": sessions[session_id].viewers,
            }
            for session_id in order
        ]

    @app.patch("/sessions/{session_id}/link")
    async def configure_link(session_id: str, patch: LinkPatch) -> dict:
        session = get_session(session_id)
        try:
            session.link.configure(**patch.model_dump(exclude_unset=True))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return session.snapshot()

    @app.get("/sessions/{session_id}/state")
    async def state(session_id: str) -> dict:
        return get_session(session_id).snapshot()

    @app.patch("/sessions/{session_id}/midi")
    async def configure_midi(session_id: str, patch: MidiPatch) -> dict:
        session = get_session(session_id)
        try:
            session.midi.configure(patch.model_dump(exclude_unset=True))
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return session.snapshot()

    @app.post("/sessions/{session_id}/conduct")
    async def conduct(session_id: str, request: ConductRequest) -> dict:
        session = get_session(session_id)
        try:
            return await session.conduct(request.instruction)
        except ConductorBusy:
            raise HTTPException(
                429, "已有指挥请求正在处理，请稍后再试", headers={"Retry-After": "1"}
            ) from None

    @app.post("/sessions/{session_id}/preset")
    async def preset(session_id: str, request: PresetRequest) -> dict:
        return await get_session(session_id).preset(request.style)

    @app.post("/sessions/{session_id}/improvisation")
    async def improvise(session_id: str, request: ImprovisationRequest) -> dict:
        session = get_session(session_id)
        try:
            session.start_improvisation(request.instruction)
        except ConductorBusy:
            raise HTTPException(429, "正在准备一轮即兴，请稍后再试") from None
        return session.snapshot()

    @app.post("/sessions/{session_id}/composition")
    async def compose(session_id: str, request: ImprovisationRequest) -> dict:
        session = get_session(session_id)
        try:
            session.start_composition(request.instruction)
        except ConductorBusy:
            raise HTTPException(429, "已经在准备一首歌") from None
        return session.snapshot()

    @app.delete("/sessions/{session_id}/composition")
    async def clear_composition(session_id: str) -> dict:
        return await get_session(session_id).clear_composition()

    @app.patch("/sessions/{session_id}/improvisation")
    async def set_automatic_improvisation(
        session_id: str,
        request: AutomaticImprovisationRequest,
    ) -> dict:
        session = get_session(session_id)
        session.set_automatic_improvisation(request.auto)
        return session.snapshot()

    @app.post("/sessions/{session_id}/{action}")
    async def transport(session_id: str, action: Literal["play", "pause", "stop"]) -> dict:
        return await get_session(session_id).transport(action)

    @app.patch("/sessions/{session_id}/settings")
    async def settings(session_id: str, patch: SettingsPatch) -> dict:
        session = get_session(session_id)
        async with session.commands:
            try:
                session.scheduler.stage_settings(patch.model_dump(exclude_unset=True))
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            await session.plan_ahead()
            return session.snapshot()

    @app.patch("/sessions/{session_id}/tracks/{track}")
    async def track_settings(
        session_id: str,
        track: TrackId,
        patch: TrackPatch,
    ) -> dict:
        session = get_session(session_id)
        async with session.commands:
            try:
                session.scheduler.stage_track(track, patch.model_dump(exclude_unset=True))
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            await session.plan_ahead()
            return session.snapshot()

    @app.get("/sessions/{session_id}/export.mid")
    async def export(session_id: str) -> Response:
        midi = await get_session(session_id).export()
        return Response(
            midi,
            media_type="audio/midi",
            headers={"Content-Disposition": 'attachment; filename="agent-ensemble.mid"'},
        )

    @app.websocket("/sessions/{session_id}/events")
    async def events(websocket: WebSocket, session_id: str) -> None:
        session = sessions.get(session_id)
        origin = websocket.headers.get("origin")
        host = websocket.headers["host"]
        if session is None or (origin and origin != f"http://{host}"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session.viewers += 1
        try:
            with session.midi.subscribe() as wake:
                snapshot = session.snapshot()
                cursor = snapshot["sequence"]
                await websocket.send_json({"state": snapshot, "events": []})
                while True:
                    with suppress(TimeoutError):
                        async with asyncio.timeout(0.02):
                            await wake.wait()
                    wake.clear()
                    snapshot, batch = session.scheduler.frame(cursor)
                    reset = bool(batch and batch[0]["sequence"] != cursor + 1)
                    cursor = snapshot["sequence"]
                    await websocket.send_json(
                        {
                            "state": session.format_state(snapshot),
                            "events": [] if reset else batch,
                            "reset": reset,
                        }
                    )
        except (WebSocketDisconnect, OSError):
            pass
        finally:
            session.viewers -= 1

    @app.websocket("/sessions/{session_id}/agents/{track}")
    async def remote_agent(websocket: WebSocket, session_id: str, track: str) -> None:
        """Let another process play one track. See scripts/remote_agent.py."""
        session = sessions.get(session_id)
        origin = websocket.headers.get("origin")
        host = websocket.headers["host"]
        if (
            session is None
            or track not in TRACK_DEFINITIONS
            or (origin and origin != f"http://{host}")
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if track in session.remote_agents:
            await websocket.send_json({"type": "error", "message": "这条轨道已有远程乐手"})
            await websocket.close(code=1013)
            return
        try:
            hello = RemoteHello.model_validate(await websocket.receive_json())
        except (ValidationError, ValueError, WebSocketDisconnect):
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.send_json({"type": "error", "message": "首条消息须为 hello"})
                await websocket.close(code=1002)
            return
        sending = asyncio.Lock()

        async def send(message: dict) -> None:
            async with sending:
                await websocket.send_json(message)

        agent = RemoteAgent(
            cast(TrackId, track), hello.name, send, deadline=hello.deadline_ms / 1000
        )
        if track in session.remote_agents:
            await websocket.close(code=1013)
            return
        session.attach_remote(agent)
        try:
            await send(
                {
                    "type": "welcome",
                    "protocol": PROTOCOL,
                    "session": session.id,
                    "track": track,
                    "deadline_ms": hello.deadline_ms,
                }
            )
            while True:
                message = await websocket.receive_json()
                if isinstance(message, dict) and message.get("type") == "notes":
                    agent.resolve(message)
                elif isinstance(message, dict) and message.get("type") == "bye":
                    break
        except (WebSocketDisconnect, OSError, ValueError):
            pass
        finally:
            session.detach_remote(agent)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        path = frontend / "index.html"
        if not path.is_file():
            raise HTTPException(503, "Build the console with scripts/setup")
        # Revalidate the page so a rebuilt console is picked up; its assets are hashed.
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.get("/score-soundfont", include_in_schema=False)
    async def score_soundfont() -> FileResponse:
        path = ROOT / "soundfonts" / "GeneralUser-GS.sf2"
        if not path.is_file():
            raise HTTPException(404, "本地谱面音色库未安装")
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/drum-kit/{pitch}/{layer}", include_in_schema=False)
    async def drum_sample(pitch: int, layer: int) -> FileResponse:
        if not 35 <= pitch <= 59 or layer not in {0, 1}:
            raise HTTPException(404, "本地鼓组采样未安装")
        path = ROOT / ".runtime" / "supercollider" / "drum-kit" / f"{pitch}-{layer}.wav"
        if not path.is_file():
            raise HTTPException(404, "本地鼓组采样未安装")
        return FileResponse(path, media_type="audio/wav")

    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    return app
