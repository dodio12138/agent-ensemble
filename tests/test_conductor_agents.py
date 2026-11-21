import json
import stat

import httpx2
from fastapi.testclient import TestClient

from band.agents import ConductorAgents, Endpoint
from band.api import create_app
from band.conductor import Conductor

KEY = "sk-test-0123456789abcd"
LOCAL = Endpoint("http://127.0.0.1:9/v1", "local-qwen", "local-secret", True, "schema")


def mock_provider(monkeypatch, reply):
    seen = []

    def respond(request):
        seen.append((request, json.loads(request.content)))
        return reply(request)

    original = httpx2.AsyncClient

    def factory(**kwargs):
        return original(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", factory)
    return seen


def app_with_agents(tmp_path):
    conductor = Conductor(LOCAL.base_url, LOCAL.model, LOCAL.api_key, phrase_generation=True)
    agents = ConductorAgents(conductor, tmp_path / "llm", local=LOCAL, local_label="本机 Qwen")
    client = TestClient(create_app(tmp_path, agents=agents), base_url="http://127.0.0.1")
    return client, agents, conductor


def test_switching_agents_repoints_the_shared_conductor_and_never_returns_the_key(
    tmp_path, monkeypatch
):
    client, _, conductor = app_with_agents(tmp_path)
    listing = client.get("/conductor/agents").json()
    assert listing["active"] == "local"
    assert [agent["id"] for agent in listing["agents"]] == ["local", "cloud", "rules"]
    assert not listing["agents"][1]["available"]
    # Cloud needs a saved key first.
    assert client.patch("/conductor/agents", json={"id": "cloud"}).status_code == 409

    saved = client.put("/conductor/cloud", json={"provider": "deepseek", "api_key": f" {KEY}\n"})
    assert saved.status_code == 200
    assert KEY not in saved.text and saved.json()["cloud"]["key_hint"] == "…abcd"
    stored = tmp_path / "llm" / "cloud.json"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert json.loads(stored.read_text())["api_key"] == KEY

    chosen = client.patch("/conductor/agents", json={"id": "cloud"}).json()
    assert chosen["active"] == "cloud" and KEY not in json.dumps(chosen)
    assert conductor.endpoint == "https://api.deepseek.com/v1/chat/completions"
    assert conductor.model == "deepseek-chat" and conductor.json_mode == "object"
    assert not conductor.phrase_generation and not conductor.local

    client.patch("/conductor/agents", json={"id": "rules"})
    assert conductor.endpoint is None and conductor.model is None
    client.patch("/conductor/agents", json={"id": "local"})
    assert conductor.endpoint == "http://127.0.0.1:9/v1/chat/completions"
    assert conductor.phrase_generation and conductor.api_key == "local-secret"

    # A saved choice of cloud comes back after a restart; an empty key field keeps the key.
    client.patch("/conductor/agents", json={"id": "cloud"})
    client.put("/conductor/cloud", json={"provider": "deepseek", "model": "deepseek-reasoner"})
    again = ConductorAgents(Conductor(), tmp_path / "llm", local=LOCAL)
    assert again.active == "cloud" and again.conductor.model == "deepseek-reasoner"
    assert again.api_key == KEY


def test_bad_settings_and_cross_site_writes_are_refused(tmp_path):
    client, _, _ = app_with_agents(tmp_path)
    bad_key = client.put("/conductor/cloud", json={"provider": "deepseek", "api_key": "a b"})
    assert bad_key.status_code == 422
    bad_url = client.put(
        "/conductor/cloud",
        json={"provider": "custom", "base_url": "ftp://x", "model": "m", "api_key": KEY},
    )
    assert bad_url.status_code == 422
    foreign = client.put(
        "/conductor/cloud",
        json={"provider": "deepseek", "api_key": KEY},
        headers={"origin": "https://evil.example"},
    )
    assert foreign.status_code == 403
    assert not (tmp_path / "llm" / "cloud.json").exists()


def test_cloud_test_uses_json_mode_with_the_schema_in_the_prompt(tmp_path, monkeypatch):
    client, _, _ = app_with_agents(tmp_path)
    client.put("/conductor/cloud", json={"provider": "deepseek", "api_key": KEY})
    seen = mock_provider(
        monkeypatch,
        lambda _: httpx2.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}],
            },
        ),
    )
    result = client.post("/conductor/cloud/test").json()
    assert result == {"ok": True, "reason": None, "model": "deepseek-chat"}
    request, payload = seen[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert payload["response_format"] == {"type": "json_object"}
    assert '"ok"' in payload["messages"][0]["content"]


def test_a_rejected_key_reports_why(tmp_path, monkeypatch):
    client, _, _ = app_with_agents(tmp_path)
    client.put("/conductor/cloud", json={"provider": "deepseek", "api_key": KEY})
    mock_provider(monkeypatch, lambda _: httpx2.Response(401, json={"error": "bad key"}))
    result = client.post("/conductor/cloud/test").json()
    assert result["ok"] is False and result["reason"] == "rejected"
