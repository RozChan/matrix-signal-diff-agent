from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.confluence_task_store import add_sources, update_source
from core.review_store import acquire_review_lock, compute_review_stats, create_task_meta, init_review_state, update_task_meta
from core.review_table import PENDING_REVIEW_LABEL, apply_editor_changes, field_rows, pending_review_count, result_display, save_dirty_reviews
from core.task_progress import ACTIVE_STATUSES, allowed_admin_actions, beijing_time, build_task_progress, choose_default_task, overall_percent
from ui.review_table import aggrid_key, capture_grid_changes, chinese_review_stats, initialize_review_session, review_phase, selected_grid_row_id, system_difference_rows


def make_task(tmp_path: Path, task_id: str = "task1") -> Path:
    tdir = tmp_path / task_id
    create_task_meta(tdir, task_id)
    return tdir


def review_item(item_id: str, *, with_unit: bool = False, with_numeric: bool = False) -> dict:
    diffs = [{"diff_field": "信号值描述", "value_40": "Key not stored", "value_51": "SC or SK not stored", "field_type": "text"}]
    if with_unit:
        diffs.append({"diff_field": "单位", "value_40": "Nm", "value_51": "N·m", "field_type": "text"})
    if with_numeric:
        diffs.append({"diff_field": "信号长度", "value_40": "8", "value_51": "12", "field_type": "numeric"})
    return {
        "item_id": item_id, "signal_40": f"A-{item_id}", "signal_51": f"B-{item_id}",
        "source_sheet": "完全同名匹配对比结果", "diff_fields": [d["diff_field"] for d in diffs],
        "diff_field_count": len(diffs), "field_diffs": diffs, "signal_ai_judgement": "疑似可忽略",
    }


def test_progress_uses_real_counts_ai_and_status_percentages(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    add_sources(tdir, [{"version": "4.0", "url": "https://x/40", "status": "pending"}, {"version": "5.1", "url": "https://x/51", "status": "pending"}])
    update_source(tdir, "https://x/40", status="completed", downloaded_count=8, attachments=[{}] * 8)
    update_source(tdir, "https://x/51", status="downloading", downloaded_count=12, attachments=[{}] * 12, attachment_count=20)
    update_task_meta(tdir, status="running", current_stage="信号级AI辅助复核", stage_progress=73, ai_required_signal_count=156, ai_completed_signal_count=87, ai_failed_signal_count=2)
    snapshot = build_task_progress(tdir)
    assert snapshot["overall_percent"] == 73
    assert snapshot["sources"]["4.0"]["downloaded_files"] == 8
    assert snapshot["sources"]["5.1"]["total_files"] == 20
    assert snapshot["ai"]["percent"] == 55.8
    update_task_meta(tdir, status="awaiting_review", stage_progress=90)
    assert build_task_progress(tdir)["overall_percent"] == 95
    update_task_meta(tdir, status="final_exported", stage_progress=95)
    assert build_task_progress(tdir)["overall_percent"] == 100


def test_progress_stage_floor_prevents_worker_restart_regression() -> None:
    assert overall_percent({"status": "running", "current_stage": "下载5.1 Confluence矩阵", "stage_progress": 6}) == 25
    assert overall_percent({"status": "running", "current_stage": "文件接收与校验", "stage_progress": 5}) == 35


def test_default_task_selection_and_allowed_actions() -> None:
    rows = [{"task_id": "done", "status": "delivered"}, {"task_id": "active", "status": "running"}, {"task_id": "review", "status": "awaiting_review"}]
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
    assert build_task_progress(make_task(tmp_path))["overall_percent"] == 0


def test_description_and_unit_rows_have_required_columns_and_stable_ids(tmp_path: Path) -> None:
    items = [review_item("a", with_unit=True)]
    state = init_review_state(tmp_path / "review", "task", items)
    description = field_rows(items, state["items"], "信号值描述")
    units = field_rows(items, state["items"], "单位")
    assert description[0]["row_id"] == "a::信号值描述"
    assert units[0]["row_id"] == "a::单位"
    assert list(k for k in description[0] if k not in {"row_id", "item_id", "field_key", "序号"}) == [
        "EEA4.0信号名", "EEA5.1信号名", "EEA4.0信号值描述", "EEA5.1信号值描述", "AI判断结果", "人工确认"
    ]
    assert description[0]["人工确认"] == PENDING_REVIEW_LABEL
    assert pending_review_count(state) == 2


def test_numeric_signals_are_excluded_from_manual_tables_and_listed_as_system_differences(tmp_path: Path) -> None:
    items = [review_item("text"), review_item("mixed", with_numeric=True)]
    state = init_review_state(tmp_path / "review", "task", items)
    assert [row["item_id"] for row in field_rows(items, state["items"], "信号值描述")] == ["text"]
    assert pending_review_count(state) == 1
    [system_row] = system_difference_rows(items)
    assert system_row["EEA4.0信号名"] == "A-mixed"
    assert system_row["数值差异字段"] == "信号长度"


def test_review_phase_requires_descriptions_before_units(tmp_path: Path) -> None:
    items = [review_item("description"), review_item("both", with_unit=True), review_item("unit", with_unit=True)]
    # Make the third item unit-only.
    items[2]["field_diffs"] = [items[2]["field_diffs"][1]]
    items[2]["diff_fields"] = ["单位"]
    state = init_review_state(tmp_path / "review", "task", items)
    stats = compute_review_stats(items, state)
    assert stats["description_only_signals"] == 1
    assert stats["unit_only_signals"] == 1
    assert stats["description_and_unit_signals"] == 1
    assert review_phase(items, state["items"]) == ("description", 2, 2)
    for item_id in ("description", "both"):
        field = state["items"][item_id]["field_reviews"]["信号值描述"]
        field.update(result="same", reviewed=True, decision_source="manual")
    assert review_phase(items, state["items"]) == ("unit", 0, 2)
    for item_id in ("both", "unit"):
        field = state["items"][item_id]["field_reviews"]["单位"]
        field.update(result="same", reviewed=True, decision_source="manual")
    assert review_phase(items, state["items"]) == ("complete", 0, 0)


def test_binary_editor_drafts_only_mark_changed_fields() -> None:
    rows = [{"row_id": "a::信号值描述", "item_id": "a", "field_key": "信号值描述"}]
    state_items = {"a": {"field_reviews": {"信号值描述": {"result": ""}}}}
    drafts = {}
    dirty = apply_editor_changes(rows, [{**rows[0], "人工确认": result_display("信号值描述", "same")}], drafts, state_items)
    assert dirty == {"a::信号值描述"}
    assert drafts["a::信号值描述"]["result"] == "same"


def test_aggrid_key_is_stable_across_review_edits() -> None:
    assert aggrid_key("信号值描述", "task") == aggrid_key("信号值描述", "task")


def test_aggrid_changes_follow_row_id_after_frontend_sorting() -> None:
    source = [
        {"row_id": "b::信号值描述", "item_id": "b", "field_key": "信号值描述", "人工确认": PENDING_REVIEW_LABEL},
        {"row_id": "a::信号值描述", "item_id": "a", "field_key": "信号值描述", "人工确认": PENDING_REVIEW_LABEL},
    ]
    returned = [
        {**source[1], "人工确认": result_display("信号值描述", "same")},
        source[0],
    ]
    state_items = {key: {"field_reviews": {"信号值描述": {"result": ""}}} for key in ("a", "b")}
    drafts: dict = {}
    dirty = capture_grid_changes(returned, source, state_items, drafts, set())
    assert dirty == {"a::信号值描述"}
    assert drafts["a::信号值描述"]["result"] == "same"
    assert "b::信号值描述" not in drafts


def test_aggrid_detail_selection_is_single_stable_row() -> None:
    assert selected_grid_row_id([{"row_id": "a::单位"}]) == "a::单位"
    assert selected_grid_row_id([]) == ""


def test_dirty_batch_save_preserves_lock_and_revision(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "task")
    review_dir = tdir / "review"
    items = [review_item("a"), review_item("b")]
    state = init_review_state(review_dir, "task", items)
    acquire_review_lock(tdir, "session-1")
    drafts = {
        "a::信号值描述": {"item_id": "a", "field_key": "信号值描述", "result": "same"},
        "b::信号值描述": {"item_id": "b", "field_key": "信号值描述", "result": "different"},
    }
    saved = save_dirty_reviews(review_dir, "task", drafts, set(drafts), base_revision=state["revision"], session_id="session-1")
    assert saved["revision"] == state["revision"] + 2
    assert saved["items"]["a"]["field_reviews"]["信号值描述"]["result"] == "same"
    with pytest.raises(Exception):
        save_dirty_reviews(review_dir, "task", drafts, {"a::信号值描述"}, base_revision=state["revision"], session_id="session-1")


def test_chinese_stats_use_binary_labels() -> None:
    translated = chinese_review_stats({"signal_total": 12, "manual_same": 8, "manual_different": 2, "updated_at": "2026-07-22T02:00:00+00:00"})
    assert translated == {"信号总数": 12, "人工确认相同": 8, "人工确认不同": 2, "最后更新时间": "2026-07-22 10:00:00"}


def test_review_session_keys_are_initialized_before_first_render() -> None:
    session: dict = {}
    drafts_key, dirty_key, detail_key, version_key, drafts = initialize_review_session(session, "new-task")
    assert drafts_key == "review-drafts-new-task"
    assert drafts == {}
    assert session == {drafts_key: {}, dirty_key: [], detail_key: "", version_key: 0}
