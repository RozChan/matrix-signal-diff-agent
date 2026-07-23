"""Focused field-level review tables for descriptions and units."""

from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd
import streamlit as st

from core.review_store import ReviewConflictError, ReviewLockError, compute_review_stats, load_review_state, update_task_meta
from core.review_table import PENDING_REVIEW_LABEL, apply_editor_changes, field_rows, result_display, save_dirty_reviews
from core.task_progress import beijing_time


def chinese_review_stats(stats: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "signal_total": "信号总数", "field_total": "差异字段总数", "pending_manual": "待人工确认字段数",
        "manual_same": "人工确认相同", "manual_different": "人工确认不同",
        "system_different": "系统判定不同", "manual_confirmed": "人工已确认字段数", "updated_at": "最后更新时间",
    }
    return {labels.get(key, key): (beijing_time(value) if key == "updated_at" else value) for key, value in stats.items()}


def _capture_editor_changes(editor_key: str, rows: list[dict[str, Any]], state_items: dict[str, Any], drafts_key: str, dirty_key: str, detail_key: str, version_key: str) -> None:
    widget_state = st.session_state.get(editor_key) or {}
    edited_rows = widget_state.get("edited_rows") or {}
    changed: list[dict[str, Any]] = []
    selected = str(st.session_state.get(detail_key) or "")
    for raw_index, patch in edited_rows.items():
        try:
            row = dict(rows[int(raw_index)])
        except (IndexError, TypeError, ValueError):
            continue
        row.update(patch)
        changed.append(row)
        if "详情" in patch:
            selected = row["row_id"] if patch["详情"] else ("" if selected == row["row_id"] else selected)
    drafts = st.session_state.setdefault(drafts_key, {})
    dirty = set(st.session_state.setdefault(dirty_key, []))
    changed_ids = {row["row_id"] for row in changed}
    dirty.difference_update(changed_ids)
    dirty.update(apply_editor_changes(rows, changed, drafts, state_items))
    st.session_state[dirty_key] = sorted(dirty)
    st.session_state[detail_key] = selected
    st.session_state[version_key] = int(st.session_state.get(version_key, 0)) + 1


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


def _render_field_table(field_name: str, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], can_edit: bool, drafts_key: str, dirty_key: str, detail_key: str, version_key: str) -> None:
    state_items = state.get("items", {})
    drafts = st.session_state.setdefault(drafts_key, {})
    rows = field_rows(items, state_items, field_name, drafts)
    if not rows:
        st.info(f"本任务没有{field_name}差异。")
        return

    c1, c2, c3 = st.columns([2.2, .8, .9])
    search = c1.text_input("搜索信号名", key=f"field-search-{field_name}-{task_id}")
    page_size = int(c2.number_input("每页条数", 1, 500, 20, key=f"field-size-{field_name}-{task_id}"))
    status = c3.selectbox("确认状态", ["待确认", "查看全部", "已确认"], key=f"field-status-{field_name}-{task_id}")
    needle = search.strip().casefold()
    filtered = [row for row in rows if (not needle or needle in row["EEA4.0信号名"].casefold() or needle in row["EEA5.1信号名"].casefold())]
    if status == "待确认":
        filtered = [row for row in filtered if row["人工确认"] == PENDING_REVIEW_LABEL]
    elif status == "已确认":
        filtered = [row for row in filtered if row["人工确认"] != PENDING_REVIEW_LABEL]

    pages = max(1, math.ceil(len(filtered) / page_size))
    page_key = f"field-page-{field_name}-{task_id}"
    page = max(1, min(int(st.session_state.get(page_key, 1)), pages))
    st.session_state[page_key] = page
    p1, p2, p3, spacer = st.columns([.55, .7, .55, 8])
    if p1.button("◀", disabled=page == 1, key=f"prev-{field_name}-{task_id}"):
        st.session_state[page_key] = page - 1
        st.rerun()
    p2.markdown(f"<div style='text-align:center;padding-top:8px'>{page}/{pages}</div>", unsafe_allow_html=True)
    if p3.button("▶", disabled=page == pages, key=f"next-{field_name}-{task_id}"):
        st.session_state[page_key] = page + 1
        st.rerun()
    start = (page - 1) * page_size
    page_rows = filtered[start:start + page_size]
    selected = str(st.session_state.get(detail_key) or "")
    for row in page_rows:
        row["详情"] = row["row_id"] == selected
    frame = pd.DataFrame(page_rows)
    editor_key = f"field-editor-{field_name}-{task_id}-{page}-{int(st.session_state.get(version_key, 0))}"
    st.data_editor(
        frame, hide_index=True, width="stretch", height=min(720, 38 * (len(page_rows) + 1) + 8),
        disabled=["row_id", "item_id", "field_key", "序号", "EEA4.0信号名", "EEA5.1信号名", f"EEA4.0{field_name}", f"EEA5.1{field_name}", "AI判断结果", *([] if can_edit else ["人工确认"])],
        column_config={
            "row_id": None, "item_id": None, "field_key": None, "序号": None,
            "EEA4.0信号名": st.column_config.TextColumn("EEA4.0信号名", width=170),
            "EEA5.1信号名": st.column_config.TextColumn("EEA5.1信号名", width=170),
            f"EEA4.0{field_name}": st.column_config.TextColumn(f"EEA4.0{field_name}", width=300),
            f"EEA5.1{field_name}": st.column_config.TextColumn(f"EEA5.1{field_name}", width=300),
            "AI判断结果": st.column_config.TextColumn("AI判断结果", width=115),
            "人工确认": st.column_config.SelectboxColumn("人工确认", options=[PENDING_REVIEW_LABEL, result_display(field_name, "same"), result_display(field_name, "different")], required=True, width=160),
            "详情": st.column_config.CheckboxColumn("详情", width=55),
        },
        key=editor_key, on_change=_capture_editor_changes,
        args=(editor_key, page_rows, state_items, drafts_key, dirty_key, detail_key, version_key),
    )
    st.caption(f"共{len(filtered)}条｜第{page}/{pages}页")


def render_compact_review(task_dir, review_dir, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], *, can_edit: bool, session_id: str, display_text: Callable[[Any], str]) -> tuple[dict[str, Any], int]:
    stats = compute_review_stats(items, state)
    st.caption(f"任务：{task_id}　人工已确认：{stats['manual_confirmed']}　待确认：{stats['pending_manual']}　最后保存：{beijing_time(stats['updated_at'])}")
    with st.expander("查看任务统计", expanded=False):
        st.json(chinese_review_stats(stats))

    drafts_key, dirty_key = f"review-drafts-{task_id}", f"review-dirty-{task_id}"
    detail_key, version_key = f"review-detail-{task_id}", f"review-version-{task_id}"
    has_units = bool(field_rows(items, state.get("items", {}), "单位", st.session_state[drafts_key]))
    tabs = st.tabs(["信号值描述待确认清单", "单位待确认清单"] if has_units else ["信号值描述待确认清单"])
    with tabs[0]:
        _render_field_table("信号值描述", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)
    if has_units:
        with tabs[1]:
            _render_field_table("单位", task_id, items, state, can_edit, drafts_key, dirty_key, detail_key, version_key)

    dirty = set(st.session_state.setdefault(dirty_key, []))
    if st.button("保存所有未保存修改", disabled=not can_edit or not dirty, key=f"save-fields-{task_id}", type="primary"):
        try:
            state = save_dirty_reviews(review_dir, task_id, st.session_state[drafts_key], dirty, base_revision=int(state.get("revision") or 0), session_id=session_id)
            st.session_state[dirty_key] = []
            update_task_meta(task_dir, status="reviewing")
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
