"""Compact Excel-style human review table."""

from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd
import streamlit as st

from core.review_store import ReviewConflictError, ReviewLockError, compute_review_stats, load_review_state, update_task_meta
from core.review_table import PENDING_REVIEW_LABEL, TABLE_RESULTS, apply_editor_changes, choose_exclusive_detail, pending_review_count, review_result_display, save_dirty_reviews, table_row
from core.task_progress import beijing_time


def _matches(item: dict[str, Any], review: dict[str, Any], source: str, field: str, ai: str, review_status: str, search: str) -> bool:
    if source != "全部" and item.get("source_sheet") != source:
        return False
    if field != "全部" and field not in (item.get("diff_fields") or []):
        return False
    if ai != "全部" and item.get("signal_ai_judgement") != ai:
        return False
    if review_status == "待人工确认" and review.get("reviewed"):
        return False
    if review_status == "人工已修改" and review.get("review_source") != "manual":
        return False
    if review_status == "AI判断疑似可忽略" and (review.get("reviewed") or item.get("signal_ai_judgement") != "疑似可忽略"):
        return False
    if review_status == "AI无法判断" and (review.get("reviewed") or item.get("signal_ai_judgement") not in {"无法判断", "未启用"}):
        return False
    needle = search.strip().casefold()
    return not needle or needle in str(item.get("signal_40") or "").casefold() or needle in str(item.get("signal_51") or "").casefold()


def filter_review_items(items: list[dict[str, Any]], state_items: dict[str, Any], *, source: str = "全部", field: str = "全部", ai: str = "全部", review_status: str = "待人工确认", search: str = "") -> list[dict[str, Any]]:
    return [item for item in items if _matches(item, state_items.get(item["item_id"], {}), source, field, ai, review_status, search)]


def chinese_review_stats(stats: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "total": "审核项总数", "priority_review": "AI判断疑似可忽略", "pending_manual": "待人工确认",
        "system_default_keep": "系统默认保留", "manual_modified": "人工已修改", "confirmed_real_diff": "确认真实差异",
        "ignored": "确认可忽略", "typo": "确认错别字", "semantic_same": "确认语义一致", "uncertain": "存疑待确认",
        "diff_field_total": "差异字段总数", "avg_diff_fields_per_signal": "平均每个信号差异字段数", "updated_at": "最后更新时间",
    }
    return {labels.get(key, key): (beijing_time(value) if key == "updated_at" else value) for key, value in stats.items()}


def _render_detail(item: dict[str, Any], review: dict[str, Any], display_text: Callable[[Any], str]) -> None:
    with st.expander("当前信号详细信息", expanded=True):
        left, right = st.columns(2)
        left.write(f"4.0信号名：{item.get('signal_40') or '<空>'}")
        right.write(f"5.1信号名：{item.get('signal_51') or '<空>'}")
        st.write(f"来源Sheet：{item.get('source_sheet', '')}｜差异字段：{'、'.join(item.get('diff_fields') or [])}")
        st.write(f"数值类差异：{'是' if item.get('has_numeric_diff') else '否'}｜文本类差异：{'是' if item.get('has_text_diff') else '否'}")
        for diff in item.get("field_diffs") or []:
            st.markdown(f"**{diff.get('diff_field') or '未解析'}**")
            c40, c51 = st.columns(2)
            c40.code(display_text(diff.get("value_40")), language="text")
            c51.code(display_text(diff.get("value_51")), language="text")
        st.write(f"AI判断：{item.get('signal_ai_judgement', '')}｜差异类型：{item.get('difference_type_summary', '')}｜置信度：{item.get('confidence', '')}")
        st.info(item.get("signal_ai_reason") or "无AI理由")
        st.write(f"AI建议：{item.get('signal_ai_suggested_action', '')}｜系统默认结论：{review.get('default_review_result') or '无'}")
        st.write(f"当前人工结论：{review.get('manual_review_result') or '待人工确认'}｜人工备注：{review.get('manual_note') or '无'}")
        with st.expander("原始差异点", expanded=False):
            st.code(item.get("original_diff_list", ""), language="text")


def render_compact_review(task_dir, review_dir, task_id: str, items: list[dict[str, Any]], state: dict[str, Any], *, can_edit: bool, session_id: str, display_text: Callable[[Any], str]) -> tuple[dict[str, Any], int]:
    state_items = state.get("items", {})
    stats = compute_review_stats(items, state)
    st.subheader("EEA 4.0/5.1 信号差异人工审核")
    st.caption(
        f"任务：{task_id}　已审核：{stats.get('total', 0) - stats.get('pending_manual', 0)}/{stats.get('total', 0)}　"
        f"待确认：{stats.get('pending_manual', 0)}　人工修改：{stats.get('manual_modified', 0)}　最后保存：{beijing_time(stats.get('updated_at'))}"
    )
    with st.expander("查看任务统计", expanded=False):
        st.json(chinese_review_stats(stats))

    sources = ["全部", *sorted({str(item.get("source_sheet") or "") for item in items if item.get("source_sheet")})]
    fields = ["全部", *sorted({str(field) for item in items for field in (item.get("diff_fields") or [])})]
    ai_values = ["全部", *sorted({str(item.get("signal_ai_judgement") or "") for item in items if item.get("signal_ai_judgement")})]
    f1, f2, f3, f4, f5, page_col, size_col = st.columns([1, 1, 1, 1.15, 1.25, .65, .75])
    source = f1.selectbox("来源Sheet", sources, key=f"table-source-{task_id}")
    field = f2.selectbox("差异字段", fields, key=f"table-field-{task_id}")
    ai = f3.selectbox("AI判断", ai_values, key=f"table-ai-{task_id}")
    review_status = f4.selectbox("审核状态", ["待人工确认", "AI判断疑似可忽略", "AI无法判断", "人工已修改", "查看全部"], key=f"table-status-{task_id}")
    search = f5.text_input("搜索信号名", key=f"table-search-{task_id}")
    page_size = int(size_col.selectbox("每页条数", [20, 50, 100], index=0, key=f"table-page-size-{task_id}"))
    filtered = filter_review_items(items, state_items, source=source, field=field, ai=ai, review_status=review_status, search=search)

    drafts_key = f"review-drafts-{task_id}"
    dirty_key = f"review-dirty-{task_id}"
    drafts = st.session_state.setdefault(drafts_key, {})
    dirty = set(st.session_state.setdefault(dirty_key, []))
    pages = max(1, math.ceil(len(filtered) / page_size))
    page = int(page_col.number_input("页码", 1, pages, 1, key=f"table-page-{task_id}"))
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]
    rows = [table_row(item, state_items.get(item["item_id"], {}), start + index + 1, drafts.get(item["item_id"])) for index, item in enumerate(page_items)]
    detail_key = f"review-detail-selected-{task_id}"
    editor_version_key = f"review-editor-version-{task_id}"
    selected_detail = str(st.session_state.get(detail_key) or "")
    for row in rows:
        row["详情"] = row["row_id"] == selected_detail
    frame = pd.DataFrame(rows)
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        height=min(720, 38 * (len(rows) + 1) + 8),
        disabled=["row_id", "序号", "信号名", "来源Sheet", "差异字段", "差异", "AI判断", "AI置信度", *([] if can_edit else ["审核结果", "审核备注"])],
        column_config={
            "row_id": None,
            "差异": st.column_config.TextColumn("具体差异（4.0 / 5.1）", width="large"),
            "审核结果": st.column_config.SelectboxColumn("👉 审核结果（请点击选择）", options=[PENDING_REVIEW_LABEL, *[review_result_display(result) for result in TABLE_RESULTS]], required=True, width="medium"),
            "审核备注": st.column_config.TextColumn("审核备注", width="medium"),
            "详情": st.column_config.CheckboxColumn("详情（单选）", width="small"),
        },
        key=f"review-editor-{task_id}-{page}-{page_size}-{source}-{field}-{ai}-{review_status}-{search}-{int(st.session_state.get(editor_version_key, 0))}",
    )
    page_dirty = apply_editor_changes(rows, edited.to_dict("records"), drafts, state_items) if rows else set()
    dirty.update(page_dirty)
    st.session_state[dirty_key] = sorted(dirty)
    st.caption(f"筛选结果：{len(filtered)}条｜第{page}/{pages}页｜未保存修改：{len(dirty)}条")

    checked = [str(row["row_id"]) for row in edited.to_dict("records") if row.get("详情")]
    new_detail = choose_exclusive_detail(checked, selected_detail)
    if not checked and selected_detail not in {str(row["row_id"]) for row in rows}:
        new_detail = selected_detail
    if new_detail != selected_detail:
        st.session_state[detail_key] = new_detail
        st.session_state[editor_version_key] = int(st.session_state.get(editor_version_key, 0)) + 1
        st.rerun()
    if selected_detail:
        selected_item = next((item for item in items if item["item_id"] == selected_detail), None)
        if selected_item:
            _render_detail(selected_item, state_items.get(selected_detail, {}), display_text)

    st.caption("审核修改会跨分页暂存在当前页面中；点击下方按钮会一次保存全部未保存修改。")
    if st.button("保存所有未保存修改", disabled=not can_edit or not dirty, key=f"save-all-{task_id}", type="primary"):
        try:
            state = save_dirty_reviews(review_dir, task_id, drafts, dirty, base_revision=int(state.get("revision") or 0), session_id=session_id)
            st.session_state[dirty_key] = []
            update_task_meta(task_dir, status="reviewing")
            st.success("全部暂存修改已保存。")
            st.rerun()
        except (ReviewConflictError, ReviewLockError) as exc:
            st.error(str(exc))
    latest = load_review_state(review_dir)
    return latest, len(st.session_state.get(dirty_key, []))
