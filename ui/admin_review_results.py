"""Administrator-only review diagnostics and Feishu delivery controls."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
import streamlit as st

from core.feishu_file_delivery import DELIVERY_ORDER
from core.feishu_sheet_delivery import deliver_task_result_sheets
from core.review_store import load_review_items, load_review_state, load_task_meta
from ui.review_table import render_review_stats, render_system_differences


def render_admin_review_results(task_dir: Path, show_downloads: Callable[[Path], None]) -> None:
    """Render review/result diagnostics exclusively within the admin page."""

    review_dir = task_dir / "review"
    items_path = review_dir / "review_items.json"
    state_path = review_dir / "review_state.json"
    st.subheader("审核结果查询")
    if items_path.exists() and state_path.exists():
        items = load_review_items(review_dir)
        state = load_review_state(review_dir)
        render_system_differences(items)
        render_review_stats(items, state)
    else:
        st.info("当前任务尚未生成可查询的审核数据。")
    show_downloads(task_dir)

    meta = load_task_meta(task_dir)
    delivery = dict(meta.get("feishu_sheet_delivery") or {})
    st.subheader("飞书云表格交付")
    st.write(f"交付状态：{delivery.get('status') or '未开始'}")
    st.write(f"最近更新时间：{delivery.get('updated_at') or '—'}")
    st.write(f"交付尝试次数：{int(delivery.get('attempt_count') or 0)}")
    spreadsheets = dict(delivery.get("spreadsheets") or {})
    if spreadsheets:
        st.dataframe(pd.DataFrame([
            {
                "云表格": value.get("title") or key,
                "状态": value.get("status", ""),
                "尝试次数": int(value.get("attempt_count") or 0),
                "权限": value.get("permission_status", ""),
                "链接": value.get("url", ""),
                "错误": value.get("last_error", ""),
            }
            for key in DELIVERY_ORDER
            for value in [spreadsheets.get(key) or {}]
        ]), hide_index=True, use_container_width=True)
    if delivery.get("last_error"):
        st.error(f"飞书交付错误：{delivery['last_error']}")

    if delivery.get("status") in {"failed", "ready"} and st.button("重新执行飞书云表格交付", key=f"retry-feishu-delivery-{task_dir.name}"):
        result = deliver_task_result_sheets(task_dir, force_notification=True)
        st.success("3个飞书云表格和结果卡片已交付。" if result.get("success") else f"重新交付失败：{result.get('last_error') or '未知错误'}")
        st.rerun()
