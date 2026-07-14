"""Persistent Confluence source tracking for Feishu tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir, read_json
from .review_store import update_task_meta

SOURCE_FILE = "confluence_sources.json"


def source_path(task_dir: Path) -> Path:
    return bot_dir(task_dir) / SOURCE_FILE


def load_confluence_sources(task_dir: Path, task_id: str | None = None) -> dict[str, Any]:
    return read_json(source_path(task_dir), {"task_id": task_id or task_dir.name, "sources": [], "version_40_ready": False, "version_51_ready": False, "auto_start": True})


def save_confluence_sources(task_dir: Path, data: dict[str, Any]) -> None:
    atomic_write_json(source_path(task_dir), data)
    sources = data.get("sources", [])
    update_task_meta(
        task_dir,
        source="feishu_confluence",
        input_mode="confluence_url",
        confluence_source_count=len(sources),
        confluence_page_total=sum(int(item.get("page_count") or 0) for item in sources),
        confluence_page_scanned=sum(int(item.get("page_scanned") or item.get("page_count") or 0) for item in sources),
        confluence_attachment_total=sum(int(item.get("attachment_count") or 0) for item in sources),
        confluence_downloaded_count=sum(int(item.get("downloaded_count") or 0) for item in sources),
    )


def add_source(task_dir: Path, source: dict[str, Any], auto_start: bool = True) -> dict[str, Any]:
    data = load_confluence_sources(task_dir)
    data["auto_start"] = auto_start
    existing = {(item.get("version"), item.get("mode"), item.get("url")) for item in data.get("sources", [])}
    key = (source.get("version"), source.get("mode"), source.get("url"))
    if key not in existing:
        data.setdefault("sources", []).append(source)
    save_confluence_sources(task_dir, data)
    return data


def update_source(task_dir: Path, url: str, **updates: Any) -> dict[str, Any]:
    data = load_confluence_sources(task_dir)
    for item in data.get("sources", []):
        if item.get("url") == url:
            item.update(updates)
            break
    files_40 = list((task_dir / "input" / "4.0").glob("*.xls*"))
    files_51 = list((task_dir / "input" / "5.1").glob("*.xls*"))
    data["version_40_ready"] = bool(files_40)
    data["version_51_ready"] = bool(files_51)
    save_confluence_sources(task_dir, data)
    return data
