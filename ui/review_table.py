"""Focused field-level review tables for descriptions and units."""

from __future__ import annotations

import os
from typing import Any, Callable

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, GridUpdateMode, JsCode

from core.review_store import ReviewConflictError, ReviewLockError, compute_review_stats, load_review_items, load_review_state, update_task_meta
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


class ReviewGridSyncError(ValueError):
    """Raised when editable rows cannot be mapped to stable review identities."""


def editor_key(field_name: str, task_id: str, can_edit: bool = True) -> str:
    """Keep stable, independent identities for both native review editors."""

    # Increment the suffix only when the grid schema changes. This resets stale
    # browser-side column widths once while remaining stable across normal edits.
    mode = "edit" if can_edit else "view"
    return f"review-data-editor-v1-{mode}-{field_name}-{task_id}"


def aggrid_key(field_name: str, task_id: str, can_edit: bool = True) -> str:
    """Keep stable, independent identities for both Manual AG Grid tables."""

    mode = "edit" if can_edit else "view"
    return f"review-aggrid-manual-v1-{mode}-{field_name}-{task_id}"


def review_editor_mode() -> str:
    """Select exactly one review editor, defaulting to the Manual AG Grid."""

    value = os.getenv("REVIEW_EDITOR_MODE", "aggrid_manual").strip().lower()
    return "data_editor" if value == "data_editor" else "aggrid_manual"


def manual_update_event_name(response: Any) -> str:
    """Return the collector trigger emitted by the patched Manual button."""

    event_data = getattr(response, "event_data", None)
    if not isinstance(event_data, dict):
        return ""
    return str(
        event_data.get("streamlitRerunEventTriggerName")
        or event_data.get("type")
        or ""
    )


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


def review_table_order(items: list[dict[str, Any]], state_items: dict[str, Any]) -> list[str]:
    """Return all applicable tables in their fixed on-page order."""

    return [field for field in ("信号值描述", "单位") if field_rows(items, state_items, field)]


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
    with st.expander(f"系统判定真实差异（含数值差异的信号，共{len(rows)}条）", expanded=False):
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
    """Merge editor rows by stable row_id, never by visible row position."""

    source_by_id = {str(row.get("row_id") or ""): row for row in source_rows}
    missing_ids = [index for index, row in enumerate(returned_rows) if not str(row.get("row_id") or "")]
    if missing_ids:
        raise ReviewGridSyncError(f"人工确认表返回数据缺少row_id，行索引：{missing_ids[:10]}")
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


def capture_grid_response(
    response: Any,
    source_rows: list[dict[str, Any]],
    state_items: dict[str, Any],
    drafts: dict[str, dict[str, Any]],
    dirty_ids: set[str],
) -> set[str]:
    """Capture edited rows from a mapping/DataFrame response."""

    returned = response.get("data") if hasattr(response, "get") else None
    returned_rows = returned.to_dict("records") if isinstance(returned, pd.DataFrame) else (returned or [])
    return capture_grid_changes(returned_rows, source_rows, state_items, drafts, dirty_ids)


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


def field_detail_state_key(detail_key: str, field_name: str) -> str:
    """Keep description and unit detail selections independent."""

    return f"{detail_key}-{field_name}"


def field_dirty_state_key(dirty_key: str, field_name: str) -> str:
    """Keep unsaved description and unit rows independent."""

    return f"{dirty_key}-{field_name}"


def field_drafts_state_key(drafts_key: str, field_name: str) -> str:
    """Keep description and unit draft dictionaries fully independent."""

    return f"{drafts_key}-{field_name}"


def save_button_disabled(can_edit: bool, has_changes: bool) -> bool:
    """Enable save only while this table owns real unsaved browser edits."""

    return (not can_edit) or (not has_changes)


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


def description_cell_options() -> dict[str, Any]:
    """Document native multiline display settings used by the review editor."""

    return {
        "wrapText": True, "autoHeight": True,
        "cellStyle": {
            "whiteSpace": "pre-line",
            "lineHeight": "20px",
            "paddingTop": "6px",
            "paddingBottom": "6px",
        },
    }


def review_grid_debug_enabled() -> bool:
    return os.getenv("REVIEW_GRID_DEBUG", "false").strip().lower() == "true"


def build_review_grid_options(
    frame: pd.DataFrame,
    field_name: str,
    *,
    can_edit: bool,
) -> dict[str, Any]:
    """Build the sortable, filterable, paginated Manual review grid."""

    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_default_column(
        sortable=True,
        filter=True,
        resizable=True,
        suppressHeaderMenuButton=False,
    )
    for hidden in ("row_id", "item_id", "field_key"):
        builder.configure_column(hidden, hide=True)
    builder.configure_column("序号", width=68, minWidth=62, maxWidth=74)
    layout = grid_column_layout(field_name)
    for signal_name in ("EEA4.0信号名", "EEA5.1信号名"):
        builder.configure_column(
            signal_name,
            tooltipField=signal_name,
            **layout[signal_name],
        )
    for value_name in (f"EEA4.0{field_name}", f"EEA5.1{field_name}"):
        options = {
            **layout[value_name],
            "tooltipField": value_name,
        }
        if field_name == "信号值描述":
            options.update(description_cell_options())
        builder.configure_column(value_name, **options)
    builder.configure_column(
        "AI判断结果",
        tooltipField="AI判断结果",
        **layout["AI判断结果"],
    )
    builder.configure_column(
        "人工确认",
        editable=can_edit,
        pinned="right",
        cellEditor="agSelectCellEditor",
        cellEditorParams={
            "values": [
                PENDING_REVIEW_LABEL,
                result_display(field_name, "same"),
                result_display(field_name, "different"),
            ]
        },
        cellStyle=JsCode(
            """
            function(params) {
                if (params.value && params.value.startsWith("🟢")) {
                    return {
                        color: "#137333",
                        backgroundColor: "#e6f4ea",
                        fontWeight: "700"
                    };
                }
                return {
                    color: "#b3261e",
                    backgroundColor: "#fce8e6",
                    fontWeight: "700"
                };
            }
            """
        ),
        **layout["人工确认"],
    )
    builder.configure_column(
        "详情",
        editable=True,
        pinned="right",
        cellEditor="agCheckboxCellEditor",
        cellRenderer="agCheckboxCellRenderer",
        **layout["详情"],
    )
    builder.configure_grid_options(
        pagination=True,
        paginationPageSize=10,
        paginationPageSizeSelector=[5, 10, 20, 50, 100],
        animateRows=False,
        tooltipShowDelay=250,
        rowHeight=34,
        getRowId=JsCode("function(params) { return params.data.row_id; }"),
    )
    return builder.build()


def _render_detail(item: dict[str, Any], field_key: str, review: dict[str, Any], display_text: Callable[[Any], str]) -> None:
    field_name = field_key.split("#", 1)[0]
    matching = [diff for diff in item.get("field_diffs") or [] if diff.get("diff_field") == field_name]
    occurrence = int(field_key.split("#", 1)[1]) if "#" in field_key else 1
    diff = matching[occurrence - 1] if len(matching) >= occurrence else {}
    with st.expander(f"当前{field_name}详细信息", expanded=True):
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


def _render_field_table_data_editor(field_name: str, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], can_edit: bool, drafts_key: str, dirty_key: str, detail_key: str, version_key: str, display_text: Callable[[Any], str]) -> bool:
    state_items = state.get("items", {})
    field_drafts_key = field_drafts_state_key(drafts_key, field_name)
    drafts = st.session_state.setdefault(field_drafts_key, {})
    rows = field_rows(items, state_items, field_name, drafts)
    if not rows:
        st.info(f"本任务没有{field_name}差异。")
        return False

    grid_rows = [{**row, "详情": False} for row in rows]
    frame = pd.DataFrame(grid_rows)
    field_dirty_key = field_dirty_state_key(dirty_key, field_name)
    st.session_state.setdefault(field_dirty_key, [])
    component_key = editor_key(field_name, task_id, can_edit)
    hidden_columns = {"row_id": None, "item_id": None, "field_key": None, "序号": None}
    column_config = {
        **hidden_columns,
        "人工确认": st.column_config.SelectboxColumn(
            "人工确认",
            options=[PENDING_REVIEW_LABEL, result_display(field_name, "same"), result_display(field_name, "different")],
            required=True,
            width="medium",
        ),
        "详情": st.column_config.CheckboxColumn("详情", width="small"),
        f"EEA4.0{field_name}": st.column_config.TextColumn(f"EEA4.0{field_name}", width="large"),
        f"EEA5.1{field_name}": st.column_config.TextColumn(f"EEA5.1{field_name}", width="large"),
    }
    editable_columns = {"人工确认", "详情"} if can_edit else {"详情"}
    disabled_columns = [column for column in frame.columns if column not in editable_columns]
    edited_frame = st.data_editor(
        frame,
        column_config=column_config,
        disabled=disabled_columns,
        hide_index=True,
        num_rows="fixed",
        width="stretch",
        height=min(720, (96 if field_name == "信号值描述" else 42) * min(len(rows), 20) + 42),
        key=component_key,
    )
    dirty = set(st.session_state.setdefault(field_dirty_key, []))
    returned_rows = edited_frame.to_dict("records")
    st.session_state[field_dirty_key] = sorted(capture_grid_changes(returned_rows, grid_rows, state_items, drafts, dirty))
    chosen = next((str(row.get("row_id") or "") for row in returned_rows if bool(row.get("详情"))), "")
    field_detail_key = field_detail_state_key(detail_key, field_name)
    if chosen:
        st.session_state[field_detail_key] = chosen
    elif any(bool(row.get("详情")) for row in returned_rows) is False:
        st.session_state[field_detail_key] = ""

    if review_grid_debug_enabled():
        changed_rows = []
        source_by_id = {str(row["row_id"]): row for row in grid_rows}
        for returned in returned_rows:
            source = source_by_id.get(str(returned.get("row_id") or ""), {})
            if returned.get("人工确认") != source.get("人工确认"):
                changed_rows.append({
                    "row_id": returned.get("row_id"),
                    "旧值": source.get("人工确认"),
                    "新值": returned.get("人工确认"),
                })
        with st.expander(f"{field_name}编辑同步诊断", expanded=False):
            st.json({
                "field_name": field_name,
                "component_key": component_key,
                "sync_mode": "streamlit_data_editor_return",
                "event_type": "data_editor_value_change_rerun",
                "can_edit": can_edit,
                "response_row_count": len(returned_rows),
                "returned_row_ids": [row.get("row_id") for row in returned_rows],
                "changed_rows": changed_rows,
                "drafts": drafts,
                "dirty_ids": st.session_state[field_dirty_key],
                "save_disabled": save_button_disabled(can_edit, bool(st.session_state[field_dirty_key])),
                "revision": int(state.get("revision") or 0),
                "pending_manual_count": int(compute_review_stats(items, state).get("pending_manual") or 0),
            })
    _, save_column = st.columns([8, 1])
    save_clicked = save_column.button(
        "保存修改",
        disabled=save_button_disabled(can_edit, bool(st.session_state[field_dirty_key])),
        key=f"save-{field_name}-{task_id}",
        type="primary",
        use_container_width=True,
    )
    selected = str(st.session_state.get(field_detail_key) or "")
    if "::" in selected:
        item_id, field_key = selected.split("::", 1)
        if field_key.split("#", 1)[0] == field_name:
            item = next((candidate for candidate in items if candidate.get("item_id") == item_id), None)
            if item:
                _render_detail(item, field_key, state.get("items", {}).get(item_id, {}), display_text)
    return save_clicked


def _render_field_table_aggrid(
    field_name: str,
    task_id: str,
    items: list[dict[str, Any]],
    state: dict[str, Any],
    can_edit: bool,
    drafts_key: str,
    dirty_key: str,
    detail_key: str,
    version_key: str,
    display_text: Callable[[Any], str],
) -> bool:
    """Render one isolated AG Grid and return whether Manual was submitted."""

    del version_key
    state_items = state.get("items", {})
    field_drafts_key = field_drafts_state_key(drafts_key, field_name)
    drafts = st.session_state.setdefault(field_drafts_key, {})
    rows = field_rows(items, state_items, field_name, drafts)
    if not rows:
        st.info(f"本任务没有{field_name}差异。")
        return False

    grid_rows = [{**row, "详情": False} for row in rows]
    frame = pd.DataFrame(grid_rows)
    field_dirty_key = field_dirty_state_key(dirty_key, field_name)
    dirty = set(st.session_state.setdefault(field_dirty_key, []))
    component_key = aggrid_key(field_name, task_id, can_edit)
    response = AgGrid(
        frame,
        gridOptions=build_review_grid_options(
            frame,
            field_name,
            can_edit=can_edit,
        ),
        height=min(
            720,
            (86 if field_name == "信号值描述" else 42) * min(len(rows), 15)
            + 86,
        ),
        update_mode=GridUpdateMode.MANUAL,
        update_on=[],
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        show_toolbar=True,
        show_search=True,
        show_download_button=False,
        theme="streamlit",
        key=component_key,
        try_to_convert_back_to_original_types=False,
        debug=review_grid_debug_enabled(),
    )
    event_name = manual_update_event_name(response)
    manual_submitted = event_name == "manualUpdate"
    returned = getattr(response, "data", None)
    returned_rows = (
        returned.to_dict("records")
        if isinstance(returned, pd.DataFrame)
        else list(returned or [])
    )
    if manual_submitted:
        dirty = capture_grid_changes(
            returned_rows,
            grid_rows,
            state_items,
            drafts,
            dirty,
        )
        st.session_state[field_dirty_key] = sorted(dirty)
        chosen = next(
            (
                str(row.get("row_id") or "")
                for row in returned_rows
                if bool(row.get("详情"))
            ),
            "",
        )
        st.session_state[field_detail_state_key(detail_key, field_name)] = chosen

    if review_grid_debug_enabled():
        with st.expander(f"{field_name}AG Grid Manual同步诊断", expanded=False):
            st.json(
                {
                    "field_name": field_name,
                    "component_key": component_key,
                    "sync_mode": "aggrid_manual_collector",
                    "event_name": event_name,
                    "can_edit": can_edit,
                    "response_row_count": len(returned_rows),
                    "returned_row_ids": [
                        row.get("row_id") for row in returned_rows
                    ],
                    "drafts": drafts,
                    "dirty_ids": st.session_state[field_dirty_key],
                    "revision": int(state.get("revision") or 0),
                    "pending_manual_count": int(
                        compute_review_stats(items, state).get("pending_manual") or 0
                    ),
                }
            )

    selected = str(
        st.session_state.get(field_detail_state_key(detail_key, field_name)) or ""
    )
    if "::" in selected:
        item_id, field_key = selected.split("::", 1)
        if field_key.split("#", 1)[0] == field_name:
            item = next(
                (
                    candidate
                    for candidate in items
                    if candidate.get("item_id") == item_id
                ),
                None,
            )
            if item:
                _render_detail(
                    item,
                    field_key,
                    state.get("items", {}).get(item_id, {}),
                    display_text,
                )
    return manual_submitted and can_edit


def _render_field_table(
    field_name: str,
    task_id: str,
    items: list[dict[str, Any]],
    state: dict[str, Any],
    can_edit: bool,
    drafts_key: str,
    dirty_key: str,
    detail_key: str,
    version_key: str,
    display_text: Callable[[Any], str],
) -> bool:
    renderer = (
        _render_field_table_data_editor
        if review_editor_mode() == "data_editor"
        else _render_field_table_aggrid
    )
    return renderer(
        field_name,
        task_id,
        items,
        state,
        can_edit,
        drafts_key,
        dirty_key,
        detail_key,
        version_key,
        display_text,
    )


def _save_review_changes(task_dir, review_dir, task_id: str, state: dict[str, Any], session_id: str, drafts_key: str, dirty_key: str) -> dict[str, Any]:
    dirty = set(st.session_state.setdefault(dirty_key, []))
    state = save_dirty_reviews(
        review_dir,
        task_id,
        st.session_state[drafts_key],
        dirty,
        base_revision=int(state.get("revision") or 0),
        session_id=session_id,
    )
    st.session_state[dirty_key] = []
    st.session_state[drafts_key] = {}
    saved_stats = compute_review_stats(load_review_items(review_dir), state)
    update_task_meta(
        task_dir,
        status="reviewing",
        history_reused_count=int(saved_stats.get("history_reused") or 0),
        pending_manual_count=int(saved_stats.get("pending_manual") or 0),
    )
    return state


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
    _phase, description_pending, unit_pending = review_phase(items, state_items)
    table_order = review_table_order(items, state_items)
    if description_pending or unit_pending:
        st.info(f"请完成全部人工确认：描述值剩余 {description_pending} 项，单位剩余 {unit_pending} 项。")
    else:
        st.success("所有需要人工确认的描述值和单位均已完成。")
    for field_name in table_order:
        st.subheader("信号值描述判断" if field_name == "信号值描述" else "单位判断")
        submitted = _render_field_table(field_name, task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key, display_text)
        if submitted:
            field_dirty_key = field_dirty_state_key(dirty_key, field_name)
            if not st.session_state.get(field_dirty_key):
                st.info(f"{field_name}没有需要保存的修改。")
                continue
            try:
                field_drafts_key = field_drafts_state_key(drafts_key, field_name)
                state = _save_review_changes(task_dir, review_dir, task_id, state, session_id, field_drafts_key, field_dirty_key)
            except (ReviewConflictError, ReviewLockError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"{field_name}保存失败：{exc}")
            else:
                st.success("人工确认结果已保存。")
                st.rerun()

    dirty_count = sum(len(st.session_state.get(field_dirty_state_key(dirty_key, field_name), [])) for field_name in table_order)
    return load_review_state(review_dir), dirty_count
