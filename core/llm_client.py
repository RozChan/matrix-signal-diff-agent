"""OpenAI-compatible LLM client for optional AI review.

This module never logs API keys.  Imports of optional dependencies are lazy so
core modules remain importable before dependencies are installed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class LLMConfigurationError(RuntimeError):
    """Raised when LLM is enabled but required config is missing."""


class LLMRequestError(RuntimeError):
    """Raised when an LLM request fails in a recoverable, readable way."""


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    api_key: str
    base_url: str
    model: str


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def get_llm_config() -> LLMConfig:
    """Load LLM config from .env and system environment variables."""

    _load_dotenv_if_available()
    return LLMConfig(
        enabled=_env_bool("LLM_ENABLED"),
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip(),
    )


def is_llm_enabled() -> bool:
    return get_llm_config().enabled


def validate_config(config: LLMConfig) -> None:
    if not config.enabled:
        return
    missing = []
    if not config.api_key:
        missing.append("LLM_API_KEY")
    if not config.base_url:
        missing.append("LLM_BASE_URL")
    if not config.model:
        missing.append("LLM_MODEL")
    if missing:
        raise LLMConfigurationError("LLM 已启用但缺少配置：" + ", ".join(missing))


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMRequestError("模型返回不是 JSON，且未找到 JSON 对象") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMRequestError(f"模型返回 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise LLMRequestError("模型返回 JSON 不是对象")
    return data


def call_chat_json(messages: list[dict[str, str]], temperature: float = 0.0, timeout: int = 60) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completions API and parse JSON content."""

    config = get_llm_config()
    if not config.enabled:
        raise LLMConfigurationError("LLM_ENABLED 不是 true，未调用模型")
    validate_config(config)

    try:
        import requests
    except ModuleNotFoundError as exc:
        raise LLMConfigurationError("缺少 requests 依赖，请先安装 requirements.txt") from exc

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    try:
        response = requests.post(
            _chat_completions_url(config.base_url),
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise LLMRequestError(f"LLM 请求失败：{exc}") from exc
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMRequestError(f"LLM 响应结构异常：{exc}") from exc
    except ValueError as exc:
        raise LLMRequestError(f"LLM 响应不是合法 JSON：{exc}") from exc

    return _extract_json_object(str(content))
