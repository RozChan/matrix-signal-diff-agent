"""Focused field-level review tables for descriptions and units."""

from __future__ import annotations

import importlib.util
from typing import Any, Callable

import pandas as pd
import streamlit as st

if importlib.util.find_spec("st_aggrid") is not None:
    from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, JsCode
else:  # pragma: no cover - rendered as an actionable deployment error
    AgGrid = DataReturnMode = GridOptionsBuilder = JsCode = None

from core.review_store import ReviewConflictError, ReviewLockError, compute_review_stats, load_review_state, update_task_meta
from core.review_table import PENDING_REVIEW_LABEL, apply_editor_changes, field_rows, result_display, save_dirty_reviews
from core.task_progress import beijing_time


def initialize_review_session(session_state: Any, task_id: str) -> tuple[str, str, str, str, dict[str, Any]]:
    """Initialize every task-scoped review key before the first table render."""

    drafts_key, dirty_key = f"review-drafts-{task_id}", f"review-dirty-{task_id}"
    detail_key, version_key = f"review-detail-{task_id}", f"review-version-{task_id}"
    drafts = session_state.setdefault(drafts_key, {})
    session_state.setdefault(dirty_key, [])
    session_state.setdefault(detail_key, "")
    session_state.setdefault(version_key, 0)
    return drafts_key, dirty_key, detail_key, version_key, drafts


def aggrid_key(field_name: str, task_id: str) -> str:
    """Use one stable grid identity so AG Grid retains sorting across edits."""

    # Increment the suffix only when the grid schema changes. This resets stale
    # browser-side column widths once while remaining stable across normal edits.
    return f"review-aggrid-v4-{field_name}-{task_id}"


def review_phase(items: list[dict[str, Any]], state_items: dict[str, Any]) -> tuple[str, int, int]:
    """Return the sequential manual-review phase and pending counts."""

    description_rows = field_rows(items, state_items, "信号值描述")
    unit_rows = field_rows(items, state_items, "单位")
    description_pending = sum(row["人工确认"] == PENDING_REVIEW_LABEL for row in description_rows)
    unit_pending = sum(row["人工确认"] == PENDING_REVIEW_LABEL for row in unit_rows)
    if description_pending:
        return "description", description_pending, unit_pending
    if unit_pending:
        return "unit", 0, unit_pending
    return "complete", 0, 0


def system_difference_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Show every field difference for signals selected by a numeric difference."""

    rows: list[dict[str, str]] = []
    for item in items:
        field_diffs = list(item.get("field_diffs") or [])
        numeric = [diff for diff in field_diffs if diff.get("field_type") == "numeric"]
        if not numeric:
            continue
        rows.append({
            "EEA4.0信号名": str(item.get("signal_40") or "<空>"),
            "EEA5.1信号名": str(item.get("signal_51") or "<空>"),
            "差异字段": "、".join(str(diff.get("diff_field") or "") for diff in field_diffs),
            "具体差异（4.0 / 5.1）": "｜".join(
                f"{diff.get('diff_field')}：4.0={diff.get('value_40') or '<空>'}；5.1={diff.get('value_51') or '<空>'}"
                for diff in field_diffs
            ),
            "判定结果": "系统判定不同",
        })
    return rows


def render_system_differences(items: list[dict[str, Any]]) -> None:
    rows = system_difference_rows(items)
    st.subheader(f"系统判定真实差异（含数值差异的信号，共{len(rows)}条）")
    if not rows:
        st.info("本任务没有包含数值差异的信号。")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(600, 38 * (len(rows) + 1) + 8))


def chinese_review_stats(stats: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "signal_total": "信号总数", "field_total": "差异字段总数", "pending_manual": "待人工确认字段数",
        "manual_same": "人工确认相同", "manual_different": "人工确认不同", "history_reused": "复用历史人工结论",
        "system_different": "系统判定不同", "manual_confirmed": "人工已确认字段数", "updated_at": "最后更新时间",
        "description_only_signals": "仅信号值描述差异信号数", "unit_only_signals": "仅单位差异信号数",
        "description_and_unit_signals": "信号值描述+单位差异信号数", "numeric_difference_signals": "包含数值差异信号数",
    }
    return {labels.get(key, key): (beijing_time(value) if key == "updated_at" else value) for key, value in stats.items()}


def render_review_stats(items: list[dict[str, Any]], state: dict[str, Any]) -> None:
    with st.expander("查看任务统计", expanded=False):
        st.json(chinese_review_stats(compute_review_stats(items, state)))


def capture_grid_changes(
    returned_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    state_items: dict[str, Any],
    drafts: dict[str, dict[str, Any]],
    dirty_ids: set[str],
) -> set[str]:
    """Merge AG Grid's sorted/filtered response by stable row_id, never by row position."""

    source_by_id = {str(row.get("row_id") or ""): row for row in source_rows}
    changed: list[dict[str, Any]] = []
    touched: set[str] = set()
    for returned in returned_rows:
        row_id = str(returned.get("row_id") or "")
        source = source_by_id.get(row_id)
        if not source or returned.get("人工确认") == source.get("人工确认"):
            continue
        changed.append({**source, "人工确认": returned.get("人工确认")})
        touched.add(row_id)
    dirty_ids.difference_update(touched)
    dirty_ids.update(apply_editor_changes(source_rows, changed, drafts, state_items))
    return dirty_ids


def selected_grid_row_id(selected_rows: Any) -> str:
    if selected_rows is None:
        return ""
    if isinstance(selected_rows, pd.DataFrame):
        records = selected_rows.to_dict("records")
    elif isinstance(selected_rows, list):
        records = selected_rows
    else:
        records = []
    return str(records[0].get("row_id") or "") if records else ""


def grid_column_layout(field_name: str) -> dict[str, dict[str, Any]]:
    """Return bounded responsive widths so every business column fits onscreen."""

    return {
        "详情": {"width": 58, "minWidth": 55, "maxWidth": 64},
        "EEA4.0信号名": {"flex": 0.75, "minWidth": 120, "maxWidth": 175},
        "EEA5.1信号名": {"flex": 0.75, "minWidth": 120, "maxWidth": 175},
        f"EEA4.0{field_name}": {"flex": 1, "minWidth": 220},
        f"EEA5.1{field_name}": {"flex": 1, "minWidth": 220},
        "AI判断结果": {"flex": 0.55, "minWidth": 100, "maxWidth": 125},
        "人工确认": {"width": 165, "minWidth": 155, "maxWidth": 180},
    }


def _grid_options(frame: pd.DataFrame, field_name: str, can_edit: bool) -> dict[str, Any]:
    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_default_column(sortable=True, filter=True, resizable=True, suppressHeaderMenuButton=False)
    for hidden in ("row_id", "item_id", "field_key", "序号"):
        builder.configure_column(hidden, hide=True)
    layout = grid_column_layout(field_name)
    builder.configure_column(
        "详情", header_name="详情", checkboxSelection=True, pinned="right",
        sortable=False, filter=False, **layout["详情"],
    )
    builder.configure_column("EEA4.0信号名", **layout["EEA4.0信号名"])
    builder.configure_column("EEA5.1信号名", **layout["EEA5.1信号名"])
    for value_column in (f"EEA4.0{field_name}", f"EEA5.1{field_name}"):
        builder.configure_column(
            value_column, tooltipField=value_column,
            **layout[value_column],
        )
    builder.configure_column("AI判断结果", **layout["AI判断结果"])
    builder.configure_column(
        "人工确认", pinned="right", editable=bool(can_edit),
        cellEditor="agSelectCellEditor",
        cellEditorParams={"values": [PENDING_REVIEW_LABEL, result_display(field_name, "same"), result_display(field_name, "different")]},
        cellStyle={"backgroundColor": "#fff7ed"} if can_edit else {},
        **layout["人工确认"],
    )
    builder.configure_selection(selection_mode="single", use_checkbox=False)
    builder.configure_pagination(paginationAutoPageSize=False, paginationPageSize=20)
    builder.configure_grid_options(
        getRowId=JsCode("function(params) { return params.data.row_id; }"),
        suppressRowClickSelection=True,
        rowSelection="single",
        singleClickEdit=True,
        suppressClickEdit=False,
        paginationPageSizeSelector=[10, 20, 50, 100],
        animateRows=False,
        tooltipShowDelay=300,
    )
    return builder.build()


def _render_detail(item: dict[str, Any], field_key: str, review: dict[str, Any], display_text: Callable[[Any], str]) -> None:
    field_name = field_key.split("#", 1)[0]
    matching = [diff for diff in item.get("field_diffs") or [] if diff.get("diff_field") == field_name]
    occurrence = int(field_key.split("#", 1)[1]) if "#" in field_key else 1
    diff = matching[occurrence - 1] if len(matching) >= occurrence else {}
    with st.expander("当前信号详细信息", expanded=True):
        c1, c2 = st.columns(2)
        c1.write(f"EEA4.0信号名：{item.get('signal_40') or '<空>'}")
        c2.write(f"EEA5.1信号名：{item.get('signal_51') or '<空>'}")
        c1.code(display_text(diff.get("value_40")), language="text")
        c2.code(display_text(diff.get("value_51")), language="text")
        st.write(f"字段：{field_name}｜AI判断结果：{item.get('signal_ai_judgement') or '无'}")
        st.info(item.get("signal_ai_reason") or "无AI理由")
        field_review = review.get("field_reviews", {}).get(field_key, {})
        st.write(f"当前人工确认：{result_display(field_name, field_review.get('result'))}")
        if field_review.get("decision_source") == "history_manual":
            st.caption(
                f"该结论复用自历史人工审核｜来源任务：{field_review.get('history_task_id') or '-'}"
                f"｜历史确认时间：{beijing_time(field_review.get('history_confirmed_at'))}"
            )


def _render_field_table(field_name: str, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], can_edit: bool, drafts_key: str, dirty_key: str, detail_key: str, version_key: str) -> None:
    state_items = state.get("items", {})
    drafts = st.session_state.setdefault(drafts_key, {})
    rows = field_rows(items, state_items, field_name, drafts)
    if not rows:
        st.info(f"本任务没有{field_name}差异。")
        return

    if AgGrid is None:
        st.error("审核表格组件未安装，请执行 pip install -r requirements.txt 后重新启动 Streamlit。")
        return

    grid_rows = [{**row, "详情": ""} for row in rows]
    frame = pd.DataFrame(grid_rows)
    response = AgGrid(
        frame,
        gridOptions=_grid_options(frame, field_name, can_edit),
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        update_on=["cellValueChanged", "selectionChanged"],
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        reload_data=False,
        height=min(720, 42 * (min(len(rows), 20) + 2) + 48),
        theme="streamlit",
        key=aggrid_key(field_name, task_id),
    )
    returned = response.get("data") if hasattr(response, "get") else None
    returned_rows = returned.to_dict("records") if isinstance(returned, pd.DataFrame) else (returned or [])
    dirty = set(st.session_state.setdefault(dirty_key, []))
    st.session_state[dirty_key] = sorted(capture_grid_changes(returned_rows, grid_rows, state_items, drafts, dirty))
    chosen = selected_grid_row_id(response.get("selected_rows") if hasattr(response, "get") else None)
    if chosen:
        st.session_state[detail_key] = chosen
    st.caption(f"共{len(rows)}条｜可直接使用表头排序和筛选｜点击最右侧详情复选框查看完整内容")


def render_compact_review(task_dir, review_dir, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], *, can_edit: bool, session_id: str, display_text: Callable[[Any], str]) -> tuple[dict[str, Any], int]:
    stats = compute_review_stats(items, state)
    st.caption(
        f"任务：{task_id}　人工已确认：{stats['manual_confirmed']}　"
        f"历史复用：{stats['history_reused']}　待确认：{stats['pending_manual']}　"
        f"最后保存：{beijing_time(stats['updated_at'])}"
    )
    if stats["history_reused"]:
        st.success(f"已按信号名、差异字段及4.0/5.1字段值精确复用 {stats['history_reused']} 条历史人工结论；可在“查看全部”中检查或修改。")

    drafts_key, dirty_key, detail_key, version_key, _drafts = initialize_review_session(st.session_state, task_id)
    state_items = state.get("items", {})
    phase, description_pending, unit_pending = review_phase(items, state_items)
    has_descriptions = bool(field_rows(items, state_items, "信号值描述"))
    has_units = bool(field_rows(items, state_items, "单位"))
    if phase == "description":
        st.info(f"请先完成信号值描述确认；完成并保存后再进入单位确认。当前剩余 {description_pending} 项。")
        _render_field_table("信号值描述", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)
    elif phase == "unit":
        st.success("信号值描述确认已完成。")
        st.info(f"请完成单位确认。当前剩余 {unit_pending} 项。")
        _render_field_table("单位", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)
        if has_descriptions:
            with st.expander("查看或修改已完成的信号值描述确认", expanded=False):
                _render_field_table("信号值描述", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)
    else:
        st.success("所有需要人工确认的信号值描述和单位均已完成。")
        if has_descriptions:
            with st.expander("查看或修改信号值描述确认", expanded=False):
                _render_field_table("信号值描述", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)
        if has_units:
            with st.expander("查看或修改单位确认", expanded=False):
                _render_field_table("单位", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)

    dirty = set(st.session_state.setdefault(dirty_key, []))
    if st.button("保存所有未保存修改", disabled=not can_edit or not dirty, key=f"save-fields-{task_id}", type="primary"):
        try:
            state = save_dirty_reviews(review_dir, task_id, st.session_state[drafts_key], dirty, base_revision=int(state.get("revision") or 0), session_id=session_id)
            st.session_state[dirty_key] = []
            saved_stats = compute_review_stats(items, state)
            update_task_meta(
                task_dir, status="reviewing",
                history_reused_count=int(saved_stats.get("history_reused") or 0),
                pending_manual_count=int(saved_stats.get("pending_manual") or 0),
            )
            st.success("人工确认结果已保存。")
            st.rerun()
        except (ReviewConflictError, ReviewLockError) as exc:
            st.error(str(exc))

    selected = str(st.session_state.get(detail_key) or "")
    if "::" in selected:
        item_id, field_key = selected.split("::", 1)
        item = next((candidate for candidate in items if candidate.get("item_id") == item_id), None)
        if item:
            _render_detail(item, field_key, state.get("items", {}).get(item_id, {}), display_text)
    return load_review_state(review_dir), len(st.session_state.get(dirty_key, []))
