"""Administrator-only review diagnostics and Feishu delivery controls."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
import streamlit as st

from core.feishu_doc_service import ATTACHMENT_LABELS, publish_task_result_document, retry_result_notification
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
    delivery = dict(meta.get("feishu_delivery") or {})
    st.subheader("飞书云文档交付")
    st.write(f"交付状态：{delivery.get('status') or '未开始'}")
    st.write(f"最近更新时间：{delivery.get('updated_at') or '—'}")
    st.write(f"交付尝试次数：{int(delivery.get('attempt_count') or 0)}")
    if delivery.get("document_title"):
        st.write(f"文档标题：{delivery['document_title']}")
    if delivery.get("document_url"):
        st.link_button("打开飞书云文档", delivery["document_url"])
    attachments = dict(delivery.get("attachments") or {})
    if attachments:
        st.dataframe(pd.DataFrame([
            {
                "附件": value.get("display_name") or ATTACHMENT_LABELS.get(key, key),
                "状态": value.get("status", ""),
                "源文件": Path(str(value.get("file_path") or "")).name,
                "尝试次数": int(value.get("attempt_count") or 0),
                "错误": value.get("last_error", ""),
            }
            for key, value in attachments.items()
        ]), hide_index=True, use_container_width=True)
    if delivery.get("last_error"):
        st.error(f"飞书交付错误：{delivery['last_error']}")

    if delivery.get("status") in {"failed", "partial_failed"} and st.button("重新执行飞书交付", key=f"retry-feishu-delivery-{task_dir.name}"):
        result = publish_task_result_document(task_dir, notify=True)
        st.success("飞书文档已创建并交付。" if result.get("success") else f"重新交付失败：{result.get('last_error') or '未知错误'}")
        st.rerun()
    notice = dict(delivery.get("result_notification") or {})
    if delivery.get("status") in {"ready", "delivered"} and notice.get("status") != "sent" and st.button("重新发送最终通知", key=f"retry-feishu-result-notice-{task_dir.name}"):
        st.success("最终通知已发送。" if retry_result_notification(task_dir) else "最终通知发送失败。")
        st.rerun()
