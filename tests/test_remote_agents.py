import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from band.api import create_app
from band.music import MusicSettings
from band.planner import Planner
from band.remote_agents import RemoteAgent


def plan_with(reply_for, *, deadline=0.2):
    """Plan bar 0 while a fake remote process answers each request via reply_for."""

    async def run():
        planner = Planner()
        sent = []

        async def send(message):
            sent.append(message)
            reply = reply_for(message)
            if reply is not None:
                asyncio.get_running_loop().call_soon(agent.resolve, reply)

        agent = RemoteAgent("bass", "测试贝斯", send, deadline=deadline)
        planner.attach("bass", agent)
        plan = await planner.plan(0, MusicSettings(), 1)
        return plan, agent, sent, planner

    return asyncio.run(run())


def bass_line(message):
    context = message["context"]
    root = 36 + context["chord"]["bass"]
    pulse = context["pulse_ticks"]
    return {
        "type": "notes",
        "request_id": message["request_id"],
        "intent": "根音四分",
        "notes": [
            {
                "start_tick": beat * pulse,
                "duration_ticks": pulse - 20,
                "pitch": root,
                "velocity": 88,
            }
            for beat in range(context["numerator"])
        ],
    }


def test_remote_musician_plays_its_track_with_the_shared_context():
    plan, agent, sent, planner = plan_with(bass_line)
    context = sent[0]["context"]
    assert context["track"] == "bass" and context["chord"]["symbol"] == "Dm7"
    assert context["band"]["kicks"], "the drummer's proposal is shared before the bass plays"
    bass = [note for note in plan.notes if note.track_id == "bass"]
    assert [note.start_tick for note in bass] == [0, 960, 1920, 2880]
    assert {note.pitch for note in bass} == {38}
    decision = next(intent for intent in plan.intents if intent.track_id == "bass")
    assert decision.status == "ready" and decision.intent == "根音四分"
    assert "bass" not in plan.fallback_tracks
    assert agent.snapshot()["answered"] == 1
    planner.detach("bass", agent)
    assert planner.agent("bass") is not agent


def test_late_or_invalid_remote_replies_fall_back_for_that_bar_only():
    plan, agent, _, _ = plan_with(lambda message: None, deadline=0.05)
    assert "bass" in plan.fallback_tracks
    assert any(note.track_id == "bass" for note in plan.notes)
    assert agent.snapshot()["late"] == 1

    def out_of_range(message):
        reply = bass_line(message)
        reply["notes"][0]["pitch"] = 100
        return reply

    plan, agent, _, _ = plan_with(out_of_range)
    decision = next(intent for intent in plan.intents if intent.track_id == "bass")
    assert decision.status == "invalid"
    assert agent.snapshot()["invalid"] == 1
    assert {intent.track_id for intent in plan.intents if intent.status == "ready"} == {
        "drums",
        "piano",
    }


def test_websocket_agent_takes_over_a_track_until_it_disconnects(tmp_path):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1")
    with client:
        state = client.post("/sessions").json()
        session_id = state["id"]
        assert client.get("/sessions").json()[0]["id"] == session_id
        path = f"ws://127.0.0.1/sessions/{session_id}/agents/bass"
        with client.websocket_connect(path) as remote:
            remote.send_json({"type": "hello", "name": "外部贝斯", "deadline_ms": 400})
            welcome = remote.receive_json()
            assert welcome["type"] == "welcome" and welcome["track"] == "bass"
            with client.websocket_connect(path) as second:
                assert second.receive_json()["type"] == "error"
            requests = []

            def answer():
                # The remote process answers on its own thread, as a separate program would.
                while True:
                    try:
                        message = remote.receive_json()
                    except Exception:  # noqa: BLE001 - the test closed the socket
                        return
                    if message.get("type") != "plan":
                        return
                    requests.append(message)
                    remote.send_json(bass_line(message))

            responder = threading.Thread(target=answer, daemon=True)
            responder.start()
            state = client.patch(f"/sessions/{session_id}/settings", json={"seed": 7}).json()
            assert requests and requests[0]["deadline_ms"] == 400
            agent = state["remote_agents"]["bass"]
            assert agent["name"] == "外部贝斯" and agent["answered"] >= 1
            bass = [note for note in state["plans"][0]["notes"] if note["track_id"] == "bass"]
            assert {note["pitch"] for note in bass} == {38}
            remote.send_json({"type": "bye"})
            for _ in range(100):
                state = client.get(f"/sessions/{session_id}/state").json()
                if not state["remote_agents"]:
                    break
                time.sleep(0.01)
        assert state["remote_agents"] == {}
        tuba = f"ws://127.0.0.1/sessions/{session_id}/agents/tuba"
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(tuba):
            pass
