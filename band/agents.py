"""Choose which model conducts: the local model, a cloud API, or the built-in rules.

All sessions share one Conductor, so switching here changes who answers for every
session at once. The cloud API key is stored only on this machine, in a file only the
current user can read, and is never sent back to the browser; the UI sees its last
four characters so the person can tell which key is saved.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from band.conductor import Conductor, JsonMode

logger = logging.getLogger(__name__)

AgentId = Literal["local", "cloud", "rules"]


@dataclass(frozen=True)
class Provider:
    label: str
    base_url: str
    model: str
    json_mode: JsonMode


# OpenAI-compatible Chat Completions endpoints. The model name can be edited in the UI.
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat", "object"),
    "dashscope": Provider(
        "通义千问",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "object",
    ),
    "moonshot": Provider("Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k", "object"),
    "zhipu": Provider("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "object"),
    "openai": Provider("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", "schema"),
    "custom": Provider("自定义（OpenAI 兼容）", "", "", "object"),
}


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: str
    api_key: str | None = None
    phrase_generation: bool = False
    json_mode: JsonMode = "schema"


def endpoint_of(conductor: Conductor) -> Endpoint | None:
    """Remember how a freshly started conductor was configured."""
    if not conductor.endpoint or not conductor.model:
        return None
    return Endpoint(
        conductor.endpoint.removesuffix("/chat/completions"),
        conductor.model,
        conductor.api_key,
        conductor.phrase_generation,
        conductor.json_mode,
    )


def _clean_key(key: str) -> str:
    key = key.strip()
    if not 8 <= len(key) <= 512 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError("API key 格式不对：应是一串不含空格的字符")
    return key


class ConductorAgents:
    def __init__(
        self,
        conductor: Conductor,
        store: Path,
        *,
        local: Endpoint | None = None,
        local_label: str = "本机模型",
    ) -> None:
        self.conductor = conductor
        self.path = store / "cloud.json"
        self.local = local
        self.local_label = local_label
        self.provider = "deepseek"
        self.base_url = PROVIDERS["deepseek"].base_url
        self.model = PROVIDERS["deepseek"].model
        self.api_key: str | None = None
        self.active: AgentId = "local" if local else "rules"
        self._load()

    # Storage -------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            provider = data.get("provider")
            if provider in PROVIDERS:
                self.provider = provider
            self.base_url = str(data.get("base_url") or self.base_url)
            self.model = str(data.get("model") or self.model)
            key = data.get("api_key")
            self.api_key = _clean_key(key) if isinstance(key, str) and key else None
            active = data.get("active")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError):
            logger.warning("Ignoring unreadable cloud conductor settings in %s", self.path)
            return
        if active == "cloud" and self.cloud_ready:
            self.select("cloud")
        elif active == "rules":
            self.select("rules")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "active": self.active,
        }
        temporary = self.path.with_suffix(".tmp")
        # Create the file readable by this user only, before the key is written to it.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    # Choosing ------------------------------------------------------------------------

    @property
    def cloud_ready(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def _cloud_endpoint(self) -> Endpoint:
        return Endpoint(
            self.base_url,
            self.model,
            self.api_key,
            json_mode=PROVIDERS[self.provider].json_mode,
        )

    def _apply(self, endpoint: Endpoint | None) -> None:
        if endpoint is None:
            self.conductor.configure()
            return
        self.conductor.configure(
            endpoint.base_url,
            endpoint.model,
            endpoint.api_key,
            phrase_generation=endpoint.phrase_generation,
            json_mode=endpoint.json_mode,
        )

    def select(self, agent: AgentId) -> None:
        if agent == "local" and self.local is None:
            raise ValueError("本机模型没有启动")
        if agent == "cloud" and not self.cloud_ready:
            raise ValueError("先保存云端 API 的地址、模型和 key")
        endpoints = {"local": self.local, "cloud": None, "rules": None}
        self._apply(self._cloud_endpoint() if agent == "cloud" else endpoints[agent])
        self.active = agent

    def choose(self, agent: AgentId) -> None:
        self.select(agent)
        self._save()

    def save_cloud(
        self, provider: str, base_url: str | None, model: str | None, api_key: str | None
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError("未知的服务商")
        preset = PROVIDERS[provider]
        base_url = (base_url or preset.base_url).strip()
        model = (model or preset.model).strip()
        if not base_url or not model:
            raise ValueError("请填写 API 地址和模型名")
        # Check the address the same way the conductor will use it.
        Conductor(base_url, model)
        if api_key:
            self.api_key = _clean_key(api_key)
        self.provider, self.base_url, self.model = provider, base_url, model
        if self.active == "cloud":
            self.select("cloud")
        self._save()

    async def test_cloud(self) -> dict:
        if not self.cloud_ready:
            return {"ok": False, "reason": "not_configured"}
        endpoint = self._cloud_endpoint()
        probe = Conductor(
            endpoint.base_url, endpoint.model, endpoint.api_key, json_mode=endpoint.json_mode
        )
        attempt = await probe.ping()
        return {
            "ok": attempt.result is True,
            "reason": attempt.reason,
            "model": attempt.model,
        }

    # Reporting -----------------------------------------------------------------------

    def describe(self) -> dict:
        key = self.api_key
        cloud_label = PROVIDERS[self.provider].label
        if self.provider == "custom" and self.model:
            cloud_label = self.model
        return {
            "active": self.active,
            "agents": [
                {
                    "id": "local",
                    "label": self.local_label,
                    "model": self.local.model if self.local else None,
                    "available": self.local is not None,
                },
                {
                    "id": "cloud",
                    "label": cloud_label,
                    "model": self.model if self.cloud_ready else None,
                    "available": self.cloud_ready,
                },
                {"id": "rules", "label": "仅规则", "model": None, "available": True},
            ],
            "cloud": {
                "provider": self.provider,
                "base_url": self.base_url,
                "model": self.model,
                "key_hint": f"…{key[-4:]}" if key else None,
            },
            "providers": {
                name: {"label": p.label, "base_url": p.base_url, "model": p.model}
                for name, p in PROVIDERS.items()
            },
        }
