"""Finalize tasks whose review state contains no pending manual fields."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .final_export import FINAL_REVIEW_FILENAME, export_final_review_result
from .result_access import ensure_result_access
from .result_notifier import build_results_zip
from .review_store import (
    compute_review_stats,
    load_review_items,
    load_review_state,
    load_task_meta,
    update_task_meta,
    utc_now_iso,
)
from .task_lock import get_task_lock


def _max_attempts() -> int:
    return max(1, int(os.getenv("AUTO_FINALIZE_MAX_ATTEMPTS", "3")))


def _claim_path(task_dir: Path) -> Path:
    return Path(task_dir) / "bot" / "auto_finalization.lock"


def _acquire_claim(task_dir: Path) -> Path | None:
    claim = _claim_path(task_dir)
    claim.parent.mkdir(parents=True, exist_ok=True)
    stale_seconds = max(60, int(os.getenv("AUTO_FINALIZE_LOCK_STALE_SECONDS", "600")))
    if claim.exists() and datetime.now().timestamp() - claim.stat().st_mtime > stale_seconds:
        claim.unlink(missing_ok=True)
    try:
        descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} at={utc_now_iso()}")
    return claim


def auto_finalize_if_no_pending(
    task_dir: str | Path,
    *,
    notify: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Generate and deliver final results when history/system decisions cover everything.

    The function is idempotent and process-safe. Tasks with any pending manual
    field are left on the existing manual-review path.
    """

    tdir = Path(task_dir).resolve()
    review_dir = tdir / "review"
    final_path = tdir / "output" / FINAL_REVIEW_FILENAME
    meta = load_task_meta(tdir)
    if meta.get("status") in {"final_exported", "delivered"} and final_path.is_file():
        return {"success": True, "already_finalized": True, "status": meta.get("status")}
    if meta.get("status") != "awaiting_review":
        return {"success": False, "skipped": True, "reason": "status_not_awaiting_review"}

    items = load_review_items(review_dir)
    state = load_review_state(review_dir)
    review_stats = compute_review_stats(items, state)
    pending = int(review_stats.get("pending_manual") or 0)
    history_reused = int(review_stats.get("history_reused") or 0)
    if pending > 0:
        return {"success": False, "skipped": True, "reason": "manual_review_required", "pending_manual": pending}
    attempts = int(meta.get("auto_finalization_attempt_count") or 0)
    if not force and attempts >= _max_attempts():
        return {"success": False, "skipped": True, "reason": "attempts_exhausted", "attempt_count": attempts}

    claim = _acquire_claim(tdir)
    if claim is None:
        return {"success": False, "running": True, "reason": "already_running"}
    try:
        with get_task_lock(tdir):
            meta = load_task_meta(tdir)
            if meta.get("status") in {"final_exported", "delivered"} and final_path.is_file():
                return {"success": True, "already_finalized": True, "status": meta.get("status")}
            if meta.get("status") != "awaiting_review":
                return {"success": False, "skipped": True, "reason": "status_not_awaiting_review"}
            attempts = int(meta.get("auto_finalization_attempt_count") or 0) + 1
            update_task_meta(
                tdir,
                review_completed=True,
                review_completed_at=utc_now_iso(),
                final_generation_status="generating",
                auto_finalization_status="running",
                auto_finalization_attempt_count=attempts,
                auto_finalization_started_at=utc_now_iso(),
                auto_finalization_error="",
                auto_finalized_without_manual=True,
                auto_finalized_by_history=history_reused > 0,
                current_stage=(
                    "历史结论已覆盖全部项目，正在生成最终结果"
                    if history_reused > 0
                    else "无需人工确认，正在生成最终结果"
                ),
                stage_progress=96,
            )

        stats = export_final_review_result(
            review_dir / "review_items.json",
            review_dir / "review_state.json",
            final_path,
        )
        from .feishu_file_delivery import register_final_result_files

        registered_files = register_final_result_files(tdir, final_path)
        meta = load_task_meta(tdir)
        updates: dict[str, Any] = {
            "status": "final_exported",
            "current_stage": "最终结果已生成",
            "stage_progress": 100,
            "final_generation_status": "done",
            "final_review_stats": stats,
            "final_result_files": registered_files,
            "auto_finalization_status": "success",
            "auto_finalization_completed_at": utc_now_iso(),
            "auto_finalization_error": "",
            "pending_manual_count": 0,
        }
        if meta.get("notify_type") == "feishu_custom_bot":
            build_results_zip(tdir)
            updates["result_delivery_status"] = "pending"
        elif meta.get("source") in {"feishu", "feishu_confluence", "auto_full_compare"}:
            updates["result_delivery_status"] = "pending"
        update_task_meta(tdir, **updates)

        delivery: dict[str, Any] = {}
        if meta.get("notify_type") == "feishu_custom_bot":
            try:
                ensure_result_access(tdir)
                if notify:
                    from .feishu_sheet_delivery import deliver_task_result_sheets

                    delivery = deliver_task_result_sheets(tdir)
            except Exception as exc:  # noqa: BLE001 - local final results remain valid
                update_task_meta(tdir, result_delivery_status="failed", delivery_error=str(exc))
                delivery = {"success": False, "last_error": str(exc)}
        return {
            "success": True,
            "status": load_task_meta(tdir).get("status"),
            "final_path": str(final_path),
            "review_stats": review_stats,
            "final_review_stats": stats,
            "delivery": delivery,
        }
    except Exception as exc:
        update_task_meta(
            tdir,
            status="awaiting_review",
            current_stage="零待审核任务自动生成结果失败",
            stage_progress=95,
            review_completed=False,
            review_completed_at="",
            final_generation_status="failed",
            auto_finalization_status="failed",
            auto_finalization_error=str(exc),
        )
        return {"success": False, "status": "awaiting_review", "error": str(exc)}
    finally:
        claim.unlink(missing_ok=True)
