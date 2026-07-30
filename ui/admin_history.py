"""Administrator UI for inspecting and governing reusable review history."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core.review_history import (
    admin_set_history_enabled,
    admin_update_history_decision,
    export_history_csv,
    history_decision_events,
    history_summary,
    list_history_decisions,
)
from core.task_progress import beijing_time


RESULT_LABELS = {"same": "相同", "different": "不同"}
ACTION_LABELS = {
    "confirm": "人工确认", "admin_correct": "管理员修正",
    "admin_disable": "管理员停用", "admin_enable": "管理员恢复",
}


def _record_label(row: dict) -> str:
    status = "启用" if int(row.get("enabled", 1)) else "停用"
    return (
        f"{row.get('signal_40') or '<空>'} ↔ {row.get('signal_51') or '<空>'}｜"
        f"{row.get('diff_field')}｜{RESULT_LABELS.get(str(row.get('result')), row.get('result'))}｜{status}｜"
        f"{str(row.get('fingerprint') or '')[:10]}"
    )


def render_admin_history(db_path: Path) -> None:
    """Render history metrics, search, detail, correction, disable and export controls."""

    st.subheader("人工审核历史库管理")
    summary = history_summary(db_path=db_path)
    metrics = st.columns(6)
    metrics[0].metric("当前记录", summary["decisions"])
    metrics[1].metric("启用复用", summary["enabled"])
    metrics[2].metric("已停用", summary["disabled"])
    metrics[3].metric("判定相同", summary["same"])
    metrics[4].metric("判定不同", summary["different"])
    metrics[5].metric("审计事件", summary["events"])
    st.caption(
        f"信号值描述：{summary['descriptions']}｜单位：{summary['units']}｜"
        f"最近更新：{beijing_time(summary['latest_confirmed_at'])}"
    )

    c1, c2, c3, c4 = st.columns([3, 1.3, 1.3, 1.3])
    search = c1.text_input("搜索历史记录", placeholder="信号名、字段值或任务编号", key="admin-history-search")
    field_label = c2.selectbox("差异字段", ["全部", "信号值描述", "单位"], key="admin-history-field")
    result_label = c3.selectbox("当前结论", ["全部", "相同", "不同"], key="admin-history-result")
    status_label = c4.selectbox("复用状态", ["全部", "启用", "停用"], key="admin-history-status")
    enabled = None if status_label == "全部" else status_label == "启用"
    result = {"相同": "same", "不同": "different"}.get(result_label, "")
    rows = list_history_decisions(
        db_path=db_path, search=search, diff_field="" if field_label == "全部" else field_label,
        result=result, enabled=enabled,
    )

    if not rows:
        st.info("没有符合条件的历史记录。")
    else:
        display = pd.DataFrame([
            {
                "EEA4.0信号名": row["signal_40"], "EEA5.1信号名": row["signal_51"],
                "字段": row["diff_field"], "当前结论": RESULT_LABELS.get(row["result"], row["result"]),
                "状态": "启用" if row["enabled"] else "停用", "最新审核人": row["latest_reviewer"],
                "最新任务": row["latest_task_id"], "确认次数": row["confirmation_count"],
                "最近更新": beijing_time(row["latest_confirmed_at"]),
            }
            for row in rows
        ])
        st.dataframe(display, hide_index=True, use_container_width=True, height=min(460, 36 * (len(rows) + 1)))
        fingerprints = [str(row["fingerprint"]) for row in rows]
        by_fingerprint = {str(row["fingerprint"]): row for row in rows}
        selected = st.selectbox(
            "查看和管理记录", fingerprints, format_func=lambda value: _record_label(by_fingerprint[value]),
            key="admin-history-selected",
        )
        row = by_fingerprint[selected]
        with st.expander("历史记录详情与审计", expanded=True):
            names = st.columns(2)
            names[0].write(f"EEA4.0信号名：{row['signal_40'] or '<空>'}")
            names[1].write(f"EEA5.1信号名：{row['signal_51'] or '<空>'}")
            st.write(f"来源Sheet：{row['source_sheet'] or '<空>'}｜差异字段：{row['diff_field']}")
            values = st.columns(2)
            values[0].code(row["value_40"] or "<空>", language="text")
            values[1].code(row["value_51"] or "<空>", language="text")
            st.caption(
                f"首次任务：{row['first_task_id']}｜最新任务：{row['latest_task_id']}｜"
                f"首次审核人：{row['first_reviewer'] or '-'}｜最新审核人：{row['latest_reviewer'] or '-'}"
            )
            events = history_decision_events(db_path=db_path, fingerprint=selected)
            event_frame = pd.DataFrame([
                {
                    "时间": beijing_time(event["confirmed_at"]),
                    "操作": ACTION_LABELS.get(event.get("action", "confirm"), event.get("action", "")),
                    "审核人": event["reviewer"], "任务": event["task_id"],
                    "旧结论": RESULT_LABELS.get(event["old_result"], event["old_result"] or "无"),
                    "新结论": RESULT_LABELS.get(event["new_result"], event["new_result"]),
                    "原因": event.get("reason", ""),
                }
                for event in events
            ])
            st.dataframe(event_frame, hide_index=True, use_container_width=True)

        st.warning("管理操作只影响未来尚未审核的精确匹配字段，不会回写已完成任务或重新生成旧结果。")
        action_cols = st.columns(2)
        with action_cols[0]:
            new_label = st.radio(
                "修正当前结论", ["相同", "不同"], horizontal=True,
                index=0 if row["result"] == "same" else 1, key=f"admin-history-result-{selected}",
            )
            correction_reason = st.text_input("修正原因", key=f"admin-history-correction-reason-{selected}")
            if st.button("确认修正结论", disabled=not correction_reason.strip(), key=f"admin-history-correct-{selected}"):
                admin_update_history_decision(
                    db_path=db_path, fingerprint=selected,
                    result={"相同": "same", "不同": "different"}[new_label],
                    actor="admin_web", reason=correction_reason,
                )
                st.success("历史结论已修正并写入审计记录。")
                st.rerun()
        with action_cols[1]:
            target_enabled = not bool(row["enabled"])
            action_name = "恢复复用" if target_enabled else "停用复用"
            status_reason = st.text_input(f"{action_name}原因", key=f"admin-history-status-reason-{selected}")
            st.caption("停用后记录和审计仍保留，但新任务不再自动复用该记录。")
            if st.button(action_name, disabled=not status_reason.strip(), key=f"admin-history-status-{selected}"):
                admin_set_history_enabled(
                    db_path=db_path, fingerprint=selected, enabled=target_enabled,
                    actor="admin_web", reason=status_reason,
                )
                st.success(f"已{action_name}。")
                st.rerun()

    downloads = st.columns(2)
    downloads[0].download_button(
        "导出当前历史结论", export_history_csv(db_path=db_path),
        file_name="review_history_decisions.csv", mime="text/csv",
    )
    downloads[1].download_button(
        "导出完整审计事件", export_history_csv(db_path=db_path, events=True),
        file_name="review_history_events.csv", mime="text/csv",
    )
