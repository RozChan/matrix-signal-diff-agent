"""Persistent Confluence source tracking for Feishu tasks."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir, read_json
from .review_store import load_task_meta, update_task_meta
from .task_lock import get_task_lock

SOURCE_FILE = "confluence_sources.json"
_TASK_LOCKS: dict[str, threading.RLock] = {}
_TASK_LOCKS_GUARD = threading.Lock()


def task_lock(task_dir: Path) -> threading.RLock:
    key = str(Path(task_dir).resolve())
    with _TASK_LOCKS_GUARD:
        lock = _TASK_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _TASK_LOCKS[key] = lock
        return lock


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def task_lock(task_dir: Path):
    return get_task_lock(task_dir)


def _default_data(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id or task_dir.name,
        "sources": [],
        "version_40_ready": False,
        "version_51_ready": False,
        "auto_start": True,
        "sources_registration_complete": True,
        "worker_starting": False,
        "worker_started": False,
        "worker_started_at": "",
        "confluence_failure_notice_status": "pending",
        "confluence_failure_notice_fingerprint": "",
        "confluence_failure_notice_sent_at": "",
        "confluence_failure_notice_error": "",
    }


def source_path(task_dir: Path) -> Path:
    return bot_dir(task_dir) / SOURCE_FILE


def load_confluence_sources(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    data = read_json(source_path(task_dir), _default_data(task_dir, task_id))
    defaults = _default_data(task_dir, task_id)
    for key, value in defaults.items():
        data.setdefault(key, value)
    return data


def save_confluence_sources(task_dir: Path, data: dict[str, Any]) -> None:
    atomic_write_json(source_path(task_dir), data)
    sources = data.get("sources", [])
    existing_meta = load_task_meta(task_dir)
    update_task_meta(
        task_dir,
        source=existing_meta.get("source") or "feishu_confluence",
        input_mode="confluence_url",
        confluence_source_count=len(sources),
        confluence_page_total=sum(int(item.get("page_count") or 0) for item in sources),
        confluence_page_scanned=sum(int(item.get("page_scanned") or item.get("page_count") or 0) for item in sources),
        confluence_attachment_total=sum(int(item.get("attachment_count") or 0) for item in sources),
        confluence_downloaded_count=sum(int(item.get("downloaded_count") or 0) for item in sources),
    )


def _update_ready_flags(task_dir: Path, data: dict[str, Any]) -> None:
    files_40 = list((task_dir / "input" / "4.0").glob("*.xls*"))
    files_51 = list((task_dir / "input" / "5.1").glob("*.xls*"))
    data["version_40_ready"] = bool(files_40)
    data["version_51_ready"] = bool(files_51)


def add_source(task_dir: Path, source: dict[str, Any], auto_start: bool = True) -> dict[str, Any]:
    add_sources(task_dir, [source], auto_start=auto_start)
    with task_lock(task_dir):
        return load_confluence_sources(task_dir)


def add_sources(task_dir: Path, sources: list[dict[str, Any]], auto_start: bool = True) -> list[dict[str, Any]]:
    with task_lock(task_dir):
        data = load_confluence_sources(task_dir)
        data["auto_start"] = auto_start
        data["sources_registration_complete"] = False
        existing = {(item.get("version"), item.get("mode"), item.get("url")) for item in data.get("sources", [])}
        added: list[dict[str, Any]] = []
        for source in sources:
            key = (source.get("version"), source.get("mode"), source.get("url"))
            if key in existing:
                continue
            item = dict(source)
            item.setdefault("status", "pending")
            item.setdefault("page_count", 0)
            item.setdefault("page_scanned", 0)
            item.setdefault("attachment_count", 0)
            item.setdefault("downloaded_count", 0)
            item.setdefault("attachments", [])
            item.setdefault("errors", [])
            data.setdefault("sources", []).append(item)
            existing.add(key)
            added.append(item)
        data["sources_registration_complete"] = True
        _update_ready_flags(task_dir, data)
        save_confluence_sources(task_dir, data)
        return added


def set_worker_state(task_dir: Path, **updates: Any) -> dict[str, Any]:
    with task_lock(task_dir):
        data = load_confluence_sources(task_dir)
        data.update(updates)
        _update_ready_flags(task_dir, data)
        save_confluence_sources(task_dir, data)
        return data


def update_source(task_dir: Path, url: str, **updates: Any) -> dict[str, Any]:
    with task_lock(task_dir):
        data = load_confluence_sources(task_dir)
        for item in data.get("sources", []):
            if item.get("url") == url:
                item.update(updates)
                break
        _update_ready_flags(task_dir, data)
        save_confluence_sources(task_dir, data)
        return data
