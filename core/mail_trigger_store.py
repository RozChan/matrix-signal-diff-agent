"""Durable IMAP baseline, per-UID status and debounce batch storage."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .bot_task_store import atomic_write_json, get_task_root, read_json, utc_now_iso

_LOCK = threading.RLock()


def state_path(root: Path | None = None) -> Path:
    return Path(root or get_task_root()) / "runtime" / "mail_trigger_state.json"


def default_state() -> dict[str, Any]:
    return {"version": 1, "uidvalidity": "", "baseline_complete": False, "baseline_uids": [], "messages": {}, "pending_batch": None, "last_poll_at": "", "last_poll_status": "never", "last_warning": ""}


def load_mail_state(root: Path | None = None) -> dict[str, Any]:
    data = read_json(state_path(root), default_state())
    result = default_state()
    if isinstance(data, dict):
        result.update(data)
    return result


def save_mail_state(data: dict[str, Any], root: Path | None = None) -> None:
    atomic_write_json(state_path(root), data)


def update_mail_state(mutator: Callable[[dict[str, Any]], Any], root: Path | None = None) -> Any:
    with _LOCK:
        data = load_mail_state(root)
        result = mutator(data)
        data["updated_at"] = utc_now_iso()
        save_mail_state(data, root)
        return result
