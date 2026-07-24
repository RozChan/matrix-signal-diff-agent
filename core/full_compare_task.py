"""Unified creation entry for command- and future email-triggered full compares."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir, get_task_root, new_task_id, read_json, task_dir, utc_now_iso
from .confluence_task_store import add_sources
from .review_store import create_task_meta, load_task_meta, update_task_meta


FULL_COMPARE_ACTIVE_STATUSES = {"created", "downloading", "ready", "running", "ai_reviewing", "generating_review", "ai_review_done"}
_INDEX_LOCK = threading.RLock()


class FullCompareConfigurationError(RuntimeError):
    pass


class FullCompareBusyError(RuntimeError):
    def __init__(self, task_id: str, stage: str = "", progress: int = 0) -> None:
        super().__init__(f"已有自动全量任务正在执行：{task_id}")
        self.task_id = task_id
        self.stage = stage
        self.progress = progress


@dataclass(frozen=True)
class FullCompareTaskResult:
    task_id: str
    task_dir: Path
    sources: tuple[dict[str, Any], ...]
    duplicate: bool = False


def full_compare_urls() -> tuple[str, str]:
    url40 = os.getenv("FULL_COMPARE_40_PARENT_URL", "").strip()
    url51 = os.getenv("FULL_COMPARE_51_PARENT_URL", "").strip()
    if not url40 or not url51:
        raise FullCompareConfigurationError("固定4.0和5.1父页面URL未完整配置")
    return url40, url51


def _trigger_index_path(root: Path) -> Path:
    return root / "runtime" / "full_compare_triggers.json"


def _active_tasks(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    active = []
    if not root.exists():
        return active
    for meta_path in root.glob("*/task_meta.json"):
        meta = read_json(meta_path, {})
        if meta.get("source") == "auto_full_compare" and meta.get("status") in FULL_COMPARE_ACTIVE_STATUSES:
            active.append((meta_path.parent, meta))
    return active


def create_full_matrix_compare_task(
    trigger_source: str,
    trigger_id: str,
    requested_by: str,
    notify_type: str,
    notify_target: str,
    trigger_metadata: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> FullCompareTaskResult:
    """Create and register both parent sources atomically; downloading is delegated.

    The function is intentionally transport-neutral. A Feishu command handler or
    a future email listener schedules the returned sources through the existing
    Confluence downloader.
    """

    if not str(trigger_id or "").strip():
        raise FullCompareConfigurationError("trigger_id不能为空")
    url40, url51 = full_compare_urls()
    if not os.getenv("CONFLUENCE_BASE_URL", "").strip() or not os.getenv("CONFLUENCE_PAT", "").strip():
        raise FullCompareConfigurationError("缺少Confluence地址或PAT凭据")
    task_root = Path(root or get_task_root())
    task_root.mkdir(parents=True, exist_ok=True)
    index_path = _trigger_index_path(task_root)
    index_key = f"{trigger_source}:{trigger_id}"
    with _INDEX_LOCK:
        index = read_json(index_path, {})
        existing_id = str(index.get(index_key, {}).get("task_id") or "")
        if existing_id:
            existing_dir = task_dir(existing_id, task_root)
            if (existing_dir / "task_meta.json").exists():
                data = load_task_meta(existing_dir)
                return FullCompareTaskResult(existing_id, existing_dir, tuple(data.get("registered_sources") or ()), True)
        max_tasks = max(1, int(os.getenv("FULL_COMPARE_MAX_CONCURRENT_TASKS", "1")))
        active = _active_tasks(task_root)
        if len(active) >= max_tasks:
            _, meta = active[0]
            raise FullCompareBusyError(str(meta.get("task_id") or active[0][0].name), str(meta.get("current_stage") or ""), int(meta.get("stage_progress") or 0))

        tid = new_task_id()
        tdir = task_dir(tid, task_root)
        for path in [tdir / "input" / "4.0", tdir / "input" / "5.1", tdir / "output", tdir / "review", bot_dir(tdir)]:
            path.mkdir(parents=True, exist_ok=True)
        create_task_meta(tdir, tid, status="created")
        strict = os.getenv("CONFLUENCE_LATEST_VERSION_STRICT", "true").strip().lower() == "true"
        select_latest = os.getenv("CONFLUENCE_PARENT_SELECT_LATEST_VERSION", "true").strip().lower() == "true"
        sources = [
            {"version": "4.0", "mode": "children_recursive", "url": url40, "status": "pending", "select_latest_version": select_latest, "latest_version_strict": strict, "errors": []},
            {"version": "5.1", "mode": "children_recursive", "url": url51, "status": "pending", "select_latest_version": select_latest, "latest_version_strict": strict, "errors": []},
        ]
        metadata = dict(trigger_metadata or {})
        update_task_meta(
            tdir,
            source="auto_full_compare",
            input_mode="confluence_parent_latest",
            trigger_source=trigger_source,
            trigger_id=str(trigger_id),
            trigger_user_open_id=metadata.get("trigger_user_open_id", requested_by if str(requested_by).startswith("ou_") else ""),
            trigger_command=metadata.get("trigger_command", ""),
            trigger_metadata=metadata,
            triggered_at=utc_now_iso(),
            requested_by=requested_by,
            notify_type=notify_type,
            notify_target=notify_target,
            feishu_sender_id=notify_target if notify_type == "user" and str(notify_target).startswith("ou_") else "",
            feishu_chat_id=notify_target if notify_type == "chat" and str(notify_target).startswith("oc_") else str(metadata.get("feishu_chat_id") or ""),
            feishu_source_message_id=str(trigger_id) if trigger_source == "feishu_command" else "",
            full_compare_40_parent_url=url40,
            full_compare_51_parent_url=url51,
            parent_select_latest_version=select_latest,
            latest_version_strict=strict,
            current_stage="任务创建",
            stage_progress=1,
            result_delivery_status="pending",
            review_completed=False,
            final_generation_status="pending",
            registered_sources=sources,
        )
        added = add_sources(tdir, sources, auto_start=True)
        # Persist the trigger only after the task and both sources are durable.
        index[index_key] = {"task_id": tid, "trigger_source": trigger_source, "trigger_id": str(trigger_id), "created_at": utc_now_iso()}
        atomic_write_json(index_path, index)
        return FullCompareTaskResult(tid, tdir, tuple(added), False)
