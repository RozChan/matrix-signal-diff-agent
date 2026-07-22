from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.confluence_task_store import add_sources, update_source
from core.review_store import acquire_review_lock, create_task_meta, init_review_state, load_review_state, update_task_meta
from core.review_table import TABLE_RESULTS, apply_editor_changes, save_dirty_reviews, table_row
from core.task_progress import ACTIVE_STATUSES, allowed_admin_actions, beijing_time, build_task_progress, choose_default_task, overall_percent
from ui.review_table import filter_review_items


def make_task(tmp_path: Path, task_id: str = "task1") -> Path:
    tdir = tmp_path / task_id
    create_task_meta(tdir, task_id)
    return tdir


def review_item(item_id: str, *, judgement: str = "疑似可忽略") -> dict:
    return {
        "item_id": item_id, "signal_40": f"A-{item_id}", "signal_51": f"B-{item_id}",
        "source_sheet": "完全同名匹配对比结果", "diff_fields": ["信号值描述"], "diff_field_count": 1,
        "signal_ai_judgement": judgement, "signal_ai_suggested_action": "建议人工确认", "confidence": "中",
    }


def test_progress_uses_real_counts_ai_and_status_percentages(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    add_sources(tdir, [
        {"version": "4.0", "url": "https://x/40", "status": "pending"},
        {"version": "5.1", "url": "https://x/51", "status": "pending"},
    ])
    update_source(tdir, "https://x/40", status="completed", downloaded_count=8, attachments=[{}] * 8)
    update_source(tdir, "https://x/51", status="downloading", downloaded_count=12, attachments=[{}] * 12, attachment_count=20)
    update_task_meta(tdir, status="running", current_stage="信号级AI辅助复核", stage_progress=73, input_40_count=8, input_51_count=12, ai_required_signal_count=156, ai_completed_signal_count=87, ai_failed_signal_count=2)
    snapshot = build_task_progress(tdir)
    assert snapshot["overall_percent"] == 73
    assert snapshot["sources"]["4.0"]["downloaded_files"] == 8
    assert snapshot["sources"]["5.1"]["total_files"] == 20
    assert snapshot["ai"] == {"total": 156, "completed": 87, "failed": 2, "percent": 55.8, "current_signal": ""}
    update_task_meta(tdir, status="awaiting_review", stage_progress=90)
    assert build_task_progress(tdir)["overall_percent"] == 95
    update_task_meta(tdir, status="final_exported", stage_progress=95)
    assert build_task_progress(tdir)["overall_percent"] == 100


def test_progress_stage_floor_prevents_worker_restart_regression() -> None:
    assert overall_percent({"status": "running", "current_stage": "下载5.1 Confluence矩阵", "stage_progress": 6}) == 25
    assert overall_percent({"status": "running", "current_stage": "文件接收与校验", "stage_progress": 5}) == 35
    assert overall_percent({"status": "running", "current_stage": "信号级AI辅助复核", "stage_progress": 70}) >= 70


def test_default_task_selection_and_allowed_actions() -> None:
    rows = [
        {"task_id": "done", "status": "delivered"},
        {"task_id": "active", "status": "running"},
        {"task_id": "review", "status": "awaiting_review"},
    ]
    assert choose_default_task(rows) == "active"
    assert choose_default_task(rows, "review") == "review"
    assert "cancel" in allowed_admin_actions("running")
    assert "cancel" not in allowed_admin_actions("awaiting_review")
    assert "retry_confluence" in allowed_admin_actions("failed")
    assert allowed_admin_actions("cancelled") == {"recreate", "details"}
    assert "running" in ACTIVE_STATUSES


def test_beijing_display_and_missing_state_fallback(tmp_path: Path) -> None:
    assert beijing_time("2026-07-22T02:04:15+00:00") == "2026-07-22 10:04:15"
    assert beijing_time("") == "-"
    snapshot = build_task_progress(make_task(tmp_path))
    assert snapshot["status_label"]
    assert snapshot["overall_percent"] == 0


def test_review_table_defaults_to_pending_and_keeps_stable_row_ids(tmp_path: Path) -> None:
    items = [review_item("pending"), review_item("default", judgement="真实差异")]
    review_dir = tmp_path / "task" / "review"
    state = init_review_state(review_dir, "task", items)
    filtered = filter_review_items(items, state["items"])
    assert [item["item_id"] for item in filtered] == ["pending"]
    all_rows = [table_row(item, state["items"][item["item_id"]], idx + 1) for idx, item in enumerate(items)]
    assert [row["row_id"] for row in all_rows] == ["pending", "default"]
    assert tuple(TABLE_RESULTS) == ("确认真实差异", "确认可忽略", "确认错别字", "确认语义一致", "存疑待确认")


def test_editor_drafts_only_mark_changed_rows() -> None:
    rows = [{"row_id": "a"}, {"row_id": "b"}]
    edited = [
        {"row_id": "a", "审核结果": "确认可忽略", "审核备注": "note", "查看详情": True},
        {"row_id": "b", "审核结果": "确认真实差异", "审核备注": "", "查看详情": False},
    ]
    state_items = {"a": {"manual_review_result": "", "manual_note": ""}, "b": {"manual_review_result": "确认真实差异", "manual_note": ""}}
    drafts = {}
    dirty = apply_editor_changes(rows, edited, drafts, state_items)
    assert dirty == {"a"}
    assert drafts["a"]["show_detail"] is True


def test_dirty_batch_save_preserves_lock_and_revision(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "task")
    review_dir = tdir / "review"
    items = [review_item("a"), review_item("b")]
    state = init_review_state(review_dir, "task", items)
    acquire_review_lock(tdir, "session-1")
    drafts = {
        "a": {"manual_review_result": "确认可忽略", "manual_note": "a"},
        "b": {"manual_review_result": "存疑待确认", "manual_note": "b"},
    }
    saved = save_dirty_reviews(review_dir, "task", drafts, {"a", "b"}, base_revision=state["revision"], session_id="session-1")
    assert saved["revision"] == state["revision"] + 2
    assert saved["items"]["a"]["manual_note"] == "a"
    with pytest.raises(Exception):
        save_dirty_reviews(review_dir, "task", drafts, {"a"}, base_revision=state["revision"], session_id="session-1")
