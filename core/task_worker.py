"""Background worker for Feishu-created matrix diff tasks."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

from core import run_all
from core.ai_review import run_ai_review
from core.bot_task_store import set_review_link, task_dir
from core.pipeline import OUTPUT_FILENAMES
from core.review_store import (
    compute_review_stats,
    generate_review_items_from_excel,
    init_review_state,
    load_review_state,
    update_task_meta,
)


def _output_dir(task_path: Path) -> Path:
    return task_path / "output"


def _review_dir(task_path: Path) -> Path:
    return task_path / "review"


def _update(task_path: Path, **updates: Any) -> None:
    update_task_meta(task_path, **updates)


def run_task(task_id: str, enable_ai: bool = True) -> dict[str, Any]:
    task_path = task_dir(task_id)
    input_40_dir = task_path / "input" / "4.0"
    input_51_dir = task_path / "input" / "5.1"
    output_dir = _output_dir(task_path)
    review_dir = _review_dir(task_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    try:
        _update(task_path, status="running", current_stage="文件接收与校验", stage_progress=5, error="")
        if not any(input_40_dir.glob("*.xls*")):
            raise ValueError("缺少 4.0 矩阵 Excel 文件")
        if not any(input_51_dir.glob("*.xls*")):
            raise ValueError("缺少 5.1 矩阵 Excel 文件")

        _update(task_path, current_stage="生成全量清单/同名去重/差异识别", stage_progress=20)
        pipeline_result = run_all(input_40_dir, input_51_dir, output_dir)
        compare_file = pipeline_result["files"].get("compare") or output_dir / OUTPUT_FILENAMES["compare"]

        _update(task_path, current_stage="信号级AI辅助复核", stage_progress=70)

        def progress_callback(payload: dict[str, Any]) -> None:
            total = int(payload.get("total") or 0)
            current = int(payload.get("current") or 0)
            pct = 70 + int((current / total) * 15) if total else 70
            _update(
                task_path,
                current_stage="信号级AI辅助复核",
                stage_progress=min(pct, 85),
                current_signal=payload.get("signal_name", ""),
                signal_total=total,
                ai_required_signal_count=int(payload.get("ai_required_total") or 0),
                ai_completed_signal_count=int(payload.get("ai_completed") or 0),
                ai_failed_signal_count=int(payload.get("failed") or 0),
            )

        ai_stats = run_ai_review(compare_file, enable_ai=enable_ai, progress_callback=progress_callback)
        _update(task_path, status="ai_review_done", current_stage="生成网页审核数据", stage_progress=88)
        review_items = generate_review_items_from_excel(compare_file, review_dir)
        state = init_review_state(review_dir, task_id, review_items)
        review_stats = compute_review_stats(review_items, state)
        meta = set_review_link(task_path)
        _update(
            task_path,
            status="awaiting_review",
            current_stage="等待人工审核",
            stage_progress=90,
            notification_status="pending",
            signal_total=len(review_items),
            ai_required_signal_count=int(ai_stats.get("ai_called_count") or 0),
            # Every attempted model call is finished at this point, including
            # calls that failed and were converted into manual-review items.
            ai_completed_signal_count=int(ai_stats.get("ai_called_count") or 0),
            ai_failed_signal_count=int(ai_stats.get("ai_failed_count") or 0),
            history_reused_count=int(review_stats.get("history_reused") or 0),
            initial_pending_manual_count=int(review_stats.get("pending_manual") or 0),
            pending_manual_count=int(review_stats.get("pending_manual") or 0),
        )
        # Dispatch from the durable worker as well as the parent-process
        # monitor.  The notification router is idempotent, so this closes the
        # gap caused by a Streamlit rerun/restart without sending duplicates.
        if int(review_stats.get("pending_manual") or 0) > 0:
            from core.notification_router import notify_review_ready

            try:
                notify_review_ready(task_path)
            except Exception as notification_error:  # noqa: BLE001 - notification must not fail completed processing
                _update(task_path, review_notification_dispatch_error=str(notification_error))
            finalization: dict[str, Any] = {}
        else:
            from core.task_finalization import auto_finalize_if_no_pending

            finalization = auto_finalize_if_no_pending(task_path, notify=True)
        return {
            "task_id": task_id,
            "review_url": meta.get("review_url"),
            "review_stats": review_stats,
            "ai_stats": ai_stats,
            "auto_finalization": finalization,
        }
    except Exception as exc:  # noqa: BLE001
        _update(task_path, status="failed", current_stage="失败", stage_progress=100, error=str(exc), traceback=traceback.format_exc())
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one matrix diff task in background")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--disable-ai", action="store_true")
    args = parser.parse_args(argv)
    run_task(args.task_id, enable_ai=not args.disable_ai)
    return 0


if __name__ == "__main__":
    sys.exit(main())
