"""Administrator real-time task progress component."""

from __future__ import annotations

import streamlit as st

from core.admin_tasks import safe_task_dir
from core.task_progress import build_task_progress, status_label


def render_task_progress(task_id: str) -> dict:
    snapshot = build_task_progress(safe_task_dir(task_id))
    st.subheader("当前任务实时进度")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**任务编号**<br>{snapshot['task_id']}", unsafe_allow_html=True)
    c2.markdown(f"**触发方式**<br>{snapshot['trigger_label']}", unsafe_allow_html=True)
    c3.markdown(f"**任务状态**<br>{snapshot['status_label']}", unsafe_allow_html=True)
    c1.markdown(f"**创建时间**<br>{snapshot['created_at_display']}", unsafe_allow_html=True)
    c2.markdown(f"**运行时长**<br>{snapshot['elapsed']}", unsafe_allow_html=True)
    c3.markdown(f"**最后更新**<br>{snapshot['updated_at_display']}", unsafe_allow_html=True)
    st.write(f"当前阶段：**{snapshot['current_stage']}**")
    if snapshot["message"]:
        st.caption(snapshot["message"])
    st.progress(snapshot["overall_percent"] / 100, text=f"总体进度：{snapshot['overall_percent']}%")
    s40, s51, ai = st.columns(3)
    for column, version in ((s40, "4.0"), (s51, "5.1")):
        source = snapshot["sources"][version]
        column.markdown(f"**{version}矩阵**")
        column.write(f"状态：{status_label(source['status'])}｜Excel：{source['downloaded_files']}/{source['total_files'] or source['input_files']}")
        column.caption(f"选中页面：{source['selected_pages']}｜失败：{source['failed_files']}｜实际输入：{source['input_files']}")
    ai_data = snapshot["ai"]
    ai.markdown("**AI辅助复核**")
    ai.write(f"进度：{ai_data['completed']}/{ai_data['total']}（{ai_data['percent']}%）")
    ai.caption(f"失败：{ai_data['failed']}｜当前信号：{ai_data['current_signal'] or '-'}")
    with st.expander("查看处理步骤", expanded=False):
        icons = {"已完成": "✅", "进行中": "🔄", "失败": "❌", "已取消": "⏹️", "未开始": "○"}
        st.markdown("  \n".join(f"{icons.get(step['state'], '○')} {step['label']}：{step['state']}" for step in snapshot["steps"]))
    with st.expander("最近执行记录", expanded=False):
        for event in snapshot["events"]:
            st.text(f"{event['time']}  {event['message']}")
    if snapshot["error"]:
        st.error(snapshot["error"])
    return snapshot


def render_live_task_progress(task_id: str, active: bool) -> dict:
    # Constructing the fragment dynamically allows terminal tasks to stop the
    # three-second timer while keeping task creation forms outside the fragment.
    fragment = st.fragment(run_every="3s" if active else None)(render_task_progress)
    return fragment(task_id)
