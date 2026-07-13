"""OpenAI-compatible LLM client for optional AI review.

This module never logs API keys. Imports of optional dependencies are lazy so
core modules remain importable before dependencies are installed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LLMConfigurationError(RuntimeError):
    """Raised when LLM is enabled but required config is missing."""


class LLMRequestError(RuntimeError):
    """Raised when an LLM request fails in a recoverable, readable way."""


class LLMTimeoutError(LLMRequestError):
    """Raised when an LLM request times out."""


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int


def _candidate_env_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    cwd = Path.cwd().resolve()
    candidates = [cwd / ".env", repo_root / ".env"]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def _load_env_file_manually(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        for env_file in _candidate_env_files():
            _load_env_file_manually(env_file)
        return

    for env_file in _candidate_env_files():
        load_dotenv(env_file, override=False)


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_llm_config() -> LLMConfig:
    """Load LLM config from .env and system environment variables."""

    _load_dotenv_if_available()
    return LLMConfig(
        enabled=_env_bool("LLM_ENABLED"),
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip(),
        timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 30),
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


def _requests_module():
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise LLMConfigurationError("缺少 requests 依赖，请先安装 requirements.txt") from exc
    return requests


def _post_chat_completion(messages: list[dict[str, str]], timeout: int | None = None, temperature: float = 0.0) -> dict[str, Any]:
    config = get_llm_config()
    if not config.enabled:
        raise LLMConfigurationError("LLM_ENABLED 不是 true，未调用模型")
    validate_config(config)

    requests = _requests_module()
    timeout_seconds = timeout or config.timeout_seconds
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
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise LLMTimeoutError("模型请求超时") from exc
    except requests.RequestException as exc:
        raise LLMRequestError(f"LLM 请求失败：{exc}") from exc
    except ValueError as exc:
        raise LLMRequestError(f"LLM 响应不是合法 JSON：{exc}") from exc


def call_chat_json(messages: list[dict[str, str]], temperature: float = 0.0, timeout: int | None = None) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completions API and parse JSON content."""

    try:
        body = _post_chat_completion(messages, timeout=timeout, temperature=temperature)
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMRequestError(f"LLM 响应结构异常：{exc}") from exc
    return _extract_json_object(str(content))


def test_llm_connection() -> dict[str, Any]:
    """Test the configured OpenAI-compatible chat completions endpoint."""

    config = get_llm_config()
    if not config.enabled:
        return {"status": "disabled", "message": "AI辅助复核未启用"}
    try:
        validate_config(config)
    except LLMConfigurationError as exc:
        return {"status": "failed", "error": str(exc)}

    messages = [
        {"role": "system", "content": "你是连接测试助手。"},
        {"role": "user", "content": '请只返回 JSON：{"ok": true}'},
    ]
    started = time.perf_counter()
    try:
        data = call_chat_json(messages, timeout=15)
    except (LLMConfigurationError, LLMRequestError) as exc:
        return {"status": "failed", "error": str(exc)}
    elapsed = round(time.perf_counter() - started, 2)
    if data.get("ok") is True:
        return {"status": "success", "model": config.model, "elapsed_seconds": elapsed}
    return {"status": "failed", "error": f"连接测试返回内容异常：{data}"}
