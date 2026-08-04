from __future__ import annotations

import inspect
from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.confluence_task_store import add_sources, update_source
from core.review_history import history_counts, history_database_path
from core.review_store import acquire_review_lock, compute_review_stats, create_task_meta, init_review_state, load_task_meta, update_task_meta
from core.review_table import PENDING_REVIEW_LABEL, apply_editor_changes, field_rows, format_multiline_enum_value, pending_review_count, result_display, result_value, save_dirty_reviews
from core.task_progress import ACTIVE_STATUSES, allowed_admin_actions, beijing_time, build_task_progress, choose_default_task, overall_percent
from ui.review_table import ReviewGridSyncError, aggrid_key, build_review_grid_options, capture_grid_changes, capture_grid_response, chinese_review_stats, description_cell_options, editor_key, field_detail_state_key, field_dirty_state_key, field_drafts_state_key, grid_column_layout, initialize_review_session, manual_update_event_name, review_editor_mode, review_phase, review_table_order, save_button_disabled, selected_detail_row_id, selected_grid_row_id, system_difference_rows
import ui.review_table as review_table_ui


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


def test_completed_ai_progress_counts_failed_calls_as_processed(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    update_task_meta(
        tdir,
        status="awaiting_review",
        ai_required_signal_count=25,
        ai_completed_signal_count=21,
        ai_failed_signal_count=4,
    )
    ai = build_task_progress(tdir)["ai"]
    assert ai == {
        "total": 25,
        "completed": 25,
        "failed": 4,
        "percent": 100.0,
        "current_signal": "",
    }


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


def test_review_labels_are_concise_and_field_specific() -> None:
    assert result_display("信号值描述", "same") == "🟢 描述值相同"
    assert result_display("信号值描述", "different") == "🟢 描述值不同"
    assert result_display("单位", "same") == "🟢 单位相同"
    assert result_display("单位", "different") == "🟢 单位不同"
    assert result_value("🟢 描述值相同") == "same"
    assert result_value("🟢 描述值不同") == "different"
    assert result_value("🟢 单位相同") == "same"
    assert result_value("🟢 单位不同") == "different"
    assert result_value(PENDING_REVIEW_LABEL) == ""


def test_enum_values_are_displayed_one_per_line_without_changing_content() -> None:
    value = "0x0: Not crank 0x1-0x2: Reserved 0x3: Crank"
    assert format_multiline_enum_value(value) == "0x0: Not crank\n0x1-0x2: Reserved\n0x3: Crank"
    assert format_multiline_enum_value("0x0: A\n0x1: B") == "0x0: A\n0x1: B"
    assert format_multiline_enum_value("") == "<空>"


def test_numeric_signals_are_excluded_from_manual_tables_and_listed_as_system_differences(tmp_path: Path) -> None:
    items = [review_item("text"), review_item("mixed", with_numeric=True)]
    state = init_review_state(tmp_path / "review", "task", items)
    assert [row["item_id"] for row in field_rows(items, state["items"], "信号值描述")] == ["text"]
    assert pending_review_count(state) == 1
    [system_row] = system_difference_rows(items)
    assert system_row["EEA4.0信号名"] == "A-mixed"
    assert system_row["差异字段"] == "信号值描述、信号长度"
    assert "信号值描述：4.0=Key not stored；5.1=SC or SK not stored" in system_row["具体差异（4.0 / 5.1）"]
    assert "信号长度：4.0=8；5.1=12" in system_row["具体差异（4.0 / 5.1）"]


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
    assert review_table_order(items, state["items"]) == ["信号值描述", "单位"]
    for item_id in ("description", "both"):
        field = state["items"][item_id]["field_reviews"]["信号值描述"]
        field.update(result="same", reviewed=True, decision_source="manual")
    assert review_phase(items, state["items"]) == ("unit", 0, 2)
    for item_id in ("both", "unit"):
        field = state["items"][item_id]["field_reviews"]["单位"]
        field.update(result="same", reviewed=True, decision_source="manual")
    assert review_phase(items, state["items"]) == ("complete", 0, 0)


def test_review_table_order_omits_unit_table_when_no_unit_differences(tmp_path: Path) -> None:
    items = [review_item("description")]
    state = init_review_state(tmp_path / "review", "task", items)
    assert review_table_order(items, state["items"]) == ["信号值描述"]


def test_binary_editor_drafts_only_mark_changed_fields() -> None:
    rows = [{"row_id": "a::信号值描述", "item_id": "a", "field_key": "信号值描述"}]
    state_items = {"a": {"field_reviews": {"信号值描述": {"result": ""}}}}
    drafts = {}
    dirty = apply_editor_changes(rows, [{**rows[0], "人工确认": result_display("信号值描述", "same")}], drafts, state_items)
    assert dirty == {"a::信号值描述"}
    assert drafts["a::信号值描述"]["result"] == "same"


def test_editor_key_is_stable_and_separate_for_both_fields() -> None:
    assert aggrid_key("信号值描述", "task", True) == aggrid_key("信号值描述", "task", True)
    assert aggrid_key("信号值描述", "task", True) != aggrid_key("信号值描述", "task", False)
    assert aggrid_key("信号值描述", "task", True) != aggrid_key("单位", "task", True)
    assert aggrid_key("单位", "task", True) != editor_key("单位", "task", True)


def test_review_editor_mode_defaults_to_manual_and_supports_emergency_fallback(monkeypatch) -> None:
    monkeypatch.delenv("REVIEW_EDITOR_MODE", raising=False)
    assert review_editor_mode() == "aggrid_manual"
    monkeypatch.setenv("REVIEW_EDITOR_MODE", "data_editor")
    assert review_editor_mode() == "data_editor"
    monkeypatch.setenv("REVIEW_EDITOR_MODE", "invalid")
    assert review_editor_mode() == "aggrid_manual"


def test_manual_update_event_name_reads_patched_collector_trigger() -> None:
    response = SimpleNamespace(
        event_data={
            "type": "manualUpdate",
            "streamlitRerunEventTriggerName": "manualUpdate",
        }
    )
    assert manual_update_event_name(response) == "manualUpdate"
    assert manual_update_event_name(SimpleNamespace(event_data=None)) == ""


def test_manual_grid_options_keep_identity_hidden_and_full_grid_features() -> None:
    rows = [{
        "row_id": "a::单位", "item_id": "a", "field_key": "单位", "序号": 1,
        "EEA4.0信号名": "A", "EEA5.1信号名": "B",
        "EEA4.0单位": "Nm", "EEA5.1单位": "N·m",
        "AI判断结果": "疑似可忽略", "人工确认": PENDING_REVIEW_LABEL, "详情": "",
    }]
    options = build_review_grid_options(pd.DataFrame(rows), "单位", can_edit=True)
    columns = {column["field"]: column for column in options["columnDefs"]}
    assert all(columns[name]["hide"] is True for name in ("row_id", "item_id", "field_key"))
    assert options["pagination"] is True
    assert options["paginationPageSize"] == 10
    assert options["paginationPageSizeSelector"] == [5, 10, 20, 50, 100]
    assert columns["人工确认"]["pinned"] == "right"
    assert columns["人工确认"]["editable"] is True
    assert columns["人工确认"]["cellEditor"] == "agSelectCellEditor"
    confirm_style = columns["人工确认"]["cellStyle"].js_code
    assert 'alignItems: "center"' in confirm_style
    assert 'justifyContent: "flex-start"' in confirm_style
    assert columns["详情"]["pinned"] == "right"
    assert columns["详情"]["checkboxSelection"] is True
    assert columns["详情"]["editable"] is False
    assert options["rowSelection"] == "single"
    assert options["suppressRowClickSelection"] is True
    assert options["columnDefs"][-1]["field"] == "详情"


def test_aggrid_column_layout_bounds_long_values_and_keeps_actions_compact() -> None:
    layout = grid_column_layout("信号值描述")
    assert layout["EEA4.0信号值描述"] == {"flex": 1, "minWidth": 220}
    assert layout["EEA5.1信号值描述"] == {"flex": 1, "minWidth": 220}
    assert layout["EEA4.0信号名"]["maxWidth"] == 175
    assert layout["人工确认"]["maxWidth"] == 180
    assert layout["详情"]["maxWidth"] == 64


def test_description_pair_uses_native_shared_auto_height_without_dom_renderer() -> None:
    options = description_cell_options()
    assert options["wrapText"] is True
    assert options["autoHeight"] is True
    assert options["cellStyle"]["whiteSpace"] == "pre-line"
    assert "cellRenderer" not in options


def test_save_button_is_not_gated_by_previous_render_dirty_state() -> None:
    assert save_button_disabled(True, True) is False
    assert save_button_disabled(True, False) is True
    assert save_button_disabled(False, True) is True
    assert field_dirty_state_key("dirty-task", "信号值描述") != field_dirty_state_key("dirty-task", "单位")
    assert field_drafts_state_key("draft-task", "信号值描述") != field_drafts_state_key("draft-task", "单位")


def test_editor_response_persists_value_change_before_save_rerun() -> None:
    source = [{"row_id": "a::单位", "item_id": "a", "field_key": "单位", "人工确认": PENDING_REVIEW_LABEL}]
    response = {"data": pd.DataFrame([{**source[0], "人工确认": result_display("单位", "same")}])}
    drafts: dict[str, dict] = {}
    state_items = {"a": {"field_reviews": {"单位": {"result": ""}}}}

    dirty = capture_grid_response(response, source, state_items, drafts, set())

    assert dirty == {"a::单位"}
    assert drafts["a::单位"]["result"] == "same"


def test_manual_submission_without_a_real_change_has_no_dirty_rows() -> None:
    source = [{
        "row_id": "a::单位",
        "item_id": "a",
        "field_key": "单位",
        "人工确认": PENDING_REVIEW_LABEL,
    }]
    drafts: dict[str, dict] = {}
    state_items = {"a": {"field_reviews": {"单位": {"result": ""}}}}
    assert capture_grid_changes(source, source, state_items, drafts, set()) == set()
    assert drafts == {}


def test_manual_submission_without_changes_has_no_persistent_info_banner() -> None:
    source = inspect.getsource(review_table_ui.render_compact_review)
    assert "没有需要保存的修改" not in source


def test_history_reuse_message_does_not_reference_missing_view_all_action() -> None:
    source = inspect.getsource(review_table_ui.render_compact_review)
    assert "查看全部" not in source


def test_editor_change_back_to_saved_value_clears_dirty_row() -> None:
    source = [{"row_id": "a::单位", "item_id": "a", "field_key": "单位", "人工确认": result_display("单位", "different")}]
    state_items = {"a": {"field_reviews": {"单位": {"result": "same"}}}}
    drafts = {"a::单位": {"item_id": "a", "field_key": "单位", "result": "different"}}
    returned = [{**source[0], "人工确认": result_display("单位", "same")}]
    assert capture_grid_changes(returned, source, state_items, drafts, {"a::单位"}) == set()
    assert drafts["a::单位"]["result"] == "same"


def test_editor_response_without_row_id_fails_loudly() -> None:
    with pytest.raises(ReviewGridSyncError, match="缺少row_id"):
        capture_grid_changes([{"人工确认": result_display("单位", "same")}], [], {}, {}, set())


def test_editor_changes_follow_row_id_after_frontend_sorting() -> None:
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
    assert field_detail_state_key("detail-task", "信号值描述") != field_detail_state_key("detail-task", "单位")


def test_aggrid_detail_selection_returns_immediately_without_manual_submit() -> None:
    selection = SimpleNamespace(
        event_data={"streamlitRerunEventTriggerName": "selectionChanged"},
        selected_rows=pd.DataFrame([{"row_id": "a::单位"}]),
    )
    cleared = SimpleNamespace(
        event_data={"streamlitRerunEventTriggerName": "selectionChanged"},
        selected_rows=None,
    )
    manual = SimpleNamespace(
        event_data={"streamlitRerunEventTriggerName": "manualUpdate"},
        selected_rows=pd.DataFrame([{"row_id": "a::单位"}]),
    )

    assert selected_detail_row_id(selection) == "a::单位"
    assert selected_detail_row_id(cleared) == ""
    assert selected_detail_row_id(manual) is None


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


def test_ui_save_updates_json_revision_reviewed_and_pending_count(tmp_path: Path, monkeypatch) -> None:
    tdir = make_task(tmp_path, "task-ui-save")
    review_dir = tdir / "review"
    items = [review_item("a", with_unit=True)]
    state = init_review_state(review_dir, "task-ui-save", items)
    (review_dir / "review_items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    acquire_review_lock(tdir, "session-1")
    drafts_key = field_drafts_state_key("review-drafts-task-ui-save", "单位")
    dirty_key = field_dirty_state_key("review-dirty-task-ui-save", "单位")
    session_state = {
        drafts_key: {"a::单位": {"item_id": "a", "field_key": "单位", "result": "different"}},
        dirty_key: ["a::单位"],
    }
    monkeypatch.setattr(review_table_ui, "st", SimpleNamespace(session_state=session_state))

    saved = review_table_ui._save_review_changes(
        tdir, review_dir, "task-ui-save", state, "session-1", drafts_key, dirty_key,
    )

    unit = saved["items"]["a"]["field_reviews"]["单位"]
    assert unit["result"] == "different" and unit["reviewed"] is True
    assert unit["reviewer"] == "session-1" and unit["reviewed_at"]
    assert saved["revision"] == state["revision"] + 1
    assert pending_review_count(saved) == 1
    assert session_state[dirty_key] == []
    assert session_state[drafts_key] == {}
    assert load_task_meta(tdir)["pending_manual_count"] == 1


def test_description_and_unit_manual_saves_are_independent_and_write_history(tmp_path: Path, monkeypatch) -> None:
    tdir = make_task(tmp_path, "task-dual-manual")
    review_dir = tdir / "review"
    items = [review_item("a", with_unit=True)]
    state = init_review_state(review_dir, "task-dual-manual", items)
    (review_dir / "review_items.json").write_text(
        json.dumps(items, ensure_ascii=False),
        encoding="utf-8",
    )
    acquire_review_lock(tdir, "session-1")
    description_drafts = field_drafts_state_key(
        "review-drafts-task-dual-manual", "信号值描述"
    )
    description_dirty = field_dirty_state_key(
        "review-dirty-task-dual-manual", "信号值描述"
    )
    unit_drafts = field_drafts_state_key("review-drafts-task-dual-manual", "单位")
    unit_dirty = field_dirty_state_key("review-dirty-task-dual-manual", "单位")
    session_state = {
        description_drafts: {
            "a::信号值描述": {
                "item_id": "a",
                "field_key": "信号值描述",
                "result": "same",
            }
        },
        description_dirty: ["a::信号值描述"],
        unit_drafts: {
            "a::单位": {
                "item_id": "a",
                "field_key": "单位",
                "result": "different",
            }
        },
        unit_dirty: ["a::单位"],
    }
    monkeypatch.setattr(
        review_table_ui,
        "st",
        SimpleNamespace(session_state=session_state),
    )

    after_description = review_table_ui._save_review_changes(
        tdir,
        review_dir,
        "task-dual-manual",
        state,
        "session-1",
        description_drafts,
        description_dirty,
    )
    assert after_description["revision"] == state["revision"] + 1
    assert pending_review_count(after_description) == 1
    assert session_state[unit_dirty] == ["a::单位"]
    assert session_state[unit_drafts]["a::单位"]["result"] == "different"

    after_unit = review_table_ui._save_review_changes(
        tdir,
        review_dir,
        "task-dual-manual",
        after_description,
        "session-1",
        unit_drafts,
        unit_dirty,
    )
    assert after_unit["revision"] == state["revision"] + 2
    assert pending_review_count(after_unit) == 0
    assert after_unit["items"]["a"]["field_reviews"]["信号值描述"]["result"] == "same"
    assert after_unit["items"]["a"]["field_reviews"]["单位"]["result"] == "different"
    assert load_task_meta(tdir)["pending_manual_count"] == 0
    assert history_counts(db_path=history_database_path(review_dir)) == {
        "decisions": 2,
        "events": 2,
    }


def test_ui_save_failure_preserves_current_table_drafts_and_dirty_rows(tmp_path: Path, monkeypatch) -> None:
    drafts_key, dirty_key = "drafts-unit", "dirty-unit"
    session_state = {
        drafts_key: {"a::单位": {"item_id": "a", "field_key": "单位", "result": "same"}},
        dirty_key: ["a::单位"],
    }
    monkeypatch.setattr(review_table_ui, "st", SimpleNamespace(session_state=session_state))
    monkeypatch.setattr(review_table_ui, "save_dirty_reviews", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        review_table_ui._save_review_changes(tmp_path, tmp_path / "review", "task", {}, "session", drafts_key, dirty_key)
    assert session_state[dirty_key] == ["a::单位"]
    assert session_state[drafts_key]["a::单位"]["result"] == "same"


def test_chinese_stats_use_binary_labels() -> None:
    translated = chinese_review_stats({"signal_total": 12, "manual_same": 8, "manual_different": 2, "updated_at": "2026-07-22T02:00:00+00:00"})
    assert translated == {"信号总数": 12, "人工确认相同": 8, "人工确认不同": 2, "最后更新时间": "2026-07-22 10:00:00"}


def test_review_session_keys_are_initialized_before_first_render() -> None:
    session: dict = {}
    drafts_key, dirty_key, detail_key, version_key, drafts = initialize_review_session(session, "new-task")
    assert drafts_key == "review-drafts-new-task"
    assert drafts == {}
    assert session == {drafts_key: {}, dirty_key: [], detail_key: "", version_key: 0}


def test_renderer_uses_exactly_one_mode_and_preserves_data_editor_fallback(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        review_table_ui,
        "_render_field_table_aggrid",
        lambda *args, **kwargs: calls.append("aggrid") or False,
    )
    monkeypatch.setattr(
        review_table_ui,
        "_render_field_table_data_editor",
        lambda *args, **kwargs: calls.append("data_editor") or False,
    )
    args = ("单位", "task", [], {}, True, "drafts", "dirty", "detail", "version", str)

    monkeypatch.delenv("REVIEW_EDITOR_MODE", raising=False)
    review_table_ui._render_field_table(*args)
    assert calls == ["aggrid"]

    calls.clear()
    monkeypatch.setenv("REVIEW_EDITOR_MODE", "data_editor")
    review_table_ui._render_field_table(*args)
    assert calls == ["data_editor"]
