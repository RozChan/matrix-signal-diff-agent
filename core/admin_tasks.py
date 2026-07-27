"""Testable backend for the Streamlit administrator task page."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir, get_task_root, read_json, scan_task_metas, utc_now_iso
from .confluence_task_store import load_confluence_sources, set_worker_state, update_source
from .full_compare_launcher import NoopEnterpriseClient, launch_full_compare_task
from .full_compare_task import FullCompareTaskResult, create_full_matrix_compare_task
from .mail_trigger_store import load_mail_state
from .review_store import load_task_meta, update_task_meta
from .task_lock import get_task_lock


def admin_token_valid(provided: str) -> bool:
    expected = os.getenv("ADMIN_PAGE_ACCESS_TOKEN", "")
    return bool(expected and provided and secrets.compare_digest(expected, provided))


def safe_task_dir(task_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id or ""):
        raise ValueError("非法task_id")
    root = get_task_root()
    candidate = (root / task_id).resolve()
    if candidate.parent != root:
        raise ValueError("非法task_id路径")
    if not (candidate / "task_meta.json").is_file():
        raise FileNotFoundError("任务不存在")
    return candidate


def create_admin_full_compare(operation_id: str) -> FullCompareTaskResult:
    result = create_full_matrix_compare_task(
        trigger_source="manual_admin",
        trigger_id=operation_id,
        requested_by="admin_web",
        notify_type="feishu_custom_bot",
        notify_target=os.getenv("FEISHU_CUSTOM_BOT_NOTIFY_TARGET", "default_group"),
        trigger_metadata={"trigger_method": "streamlit_admin", "triggered_at": utc_now_iso()},
    )
    launch_full_compare_task(result)
    return result


def list_admin_tasks(limit: int = 50) -> list[dict[str, Any]]:
    from .task_progress import beijing_time

    rows = []
    for tdir, meta in scan_task_metas():
        created_at = meta.get("created_at", meta.get("triggered_at", ""))
        rows.append({
            "task_id": meta.get("task_id", tdir.name),
            "trigger_source": meta.get("trigger_source", ""),
            "created_at": created_at,
            "created_at_display": beijing_time(created_at),
            "status": meta.get("status", ""),
            "current_stage": meta.get("current_stage", ""),
            "progress": int(meta.get("stage_progress") or 0),
            "input_40_count": int(meta.get("input_40_count") or 0),
            "input_51_count": int(meta.get("input_51_count") or 0),
            "ai_progress": f"{int(meta.get('ai_completed_signal_count') or 0)}/{int(meta.get('ai_required_signal_count') or 0)}",
            "review_url": meta.get("review_url", ""),
            "result_url": meta.get("result_url", ""),
            "result_delivery_status": meta.get("result_delivery_status", ""),
        })
    rows.sort(key=lambda item: str(item.get("created_at") or item.get("task_id")), reverse=True)
    return rows[:limit]


def _record_action(tdir: Path, action: str, actor: str) -> None:
    path = bot_dir(tdir) / "admin_actions.json"
    records = read_json(path, [])
    records.append({"action": action, "actor": actor, "source": "admin_web", "at": utc_now_iso()})
    atomic_write_json(path, records)


def cancel_admin_task(task_id: str, actor: str = "admin_web") -> bool:
    tdir = safe_task_dir(task_id)
    with get_task_lock(tdir):
        meta = load_task_meta(tdir)
        if meta.get("status") in {"cancelled", "failed", "awaiting_review", "final_exported", "delivered"}:
            return False
        update_task_meta(tdir, status="cancelled", current_stage="已取消", cancelled_at=utc_now_iso(), cancelled_by=actor)
        set_worker_state(tdir, worker_starting=False, worker_started=False, worker_error="管理员取消任务")
        _record_action(tdir, "cancel", actor)
    from bot_service import _WORKER_PROCESSES, _WORKER_PROCESSES_LOCK, _terminate_process_tree

    with _WORKER_PROCESSES_LOCK:
        process = _WORKER_PROCESSES.pop(task_id, None)
    _terminate_process_tree(process, int(meta.get("worker_pid") or 0), task_id=task_id, process_group=bool(meta.get("worker_process_group")))
    return True


def retry_admin_confluence(task_id: str, actor: str = "admin_web") -> int:
    tdir = safe_task_dir(task_id)
    with get_task_lock(tdir):
        data = load_confluence_sources(tdir, task_id)
        failed = [item for item in data.get("sources", []) if item.get("status") == "failed"]
        for item in failed:
            update_source(tdir, item.get("url", ""), status="pending", errors=[], downloaded_count=0, attachments=[])
        set_worker_state(tdir, confluence_failure_notice_status="pending", confluence_failure_notice_fingerprint="")
        _record_action(tdir, "retry_confluence", actor)
    if not failed:
        return 0
    from bot_service import _download_confluence_source
    import threading

    client = NoopEnterpriseClient()
    for item in failed:
        threading.Thread(target=_download_confluence_source, args=(task_id, tdir, {**item, "status": "pending", "errors": []}, client, ""), daemon=True).start()
    return len(failed)


def admin_system_status() -> dict[str, Any]:
    mail = load_mail_state()
    active = next((row for row in list_admin_tasks() if row["status"] in {"created", "downloading", "ready", "running", "ai_reviewing", "generating_review", "ai_review_done"}), None)
    return {
        "mail_enabled": os.getenv("MAIL_WATCHER_ENABLED", "false").lower() == "true",
        "mail_last_poll_at": mail.get("last_poll_at", ""),
        "mail_status": mail.get("last_poll_status", "never"),
        "pending_batches": 1 if mail.get("pending_batch") and mail["pending_batch"].get("status") in {"pending", "queued"} else 0,
        "active_task": active,
        "custom_bot_configured": bool(os.getenv("FEISHU_CUSTOM_BOT_WEBHOOK", "").startswith("https://")),
        "parent_40_configured": bool(os.getenv("FULL_COMPARE_40_PARENT_URL", "")),
        "parent_51_configured": bool(os.getenv("FULL_COMPARE_51_PARENT_URL", "")),
    }
