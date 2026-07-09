from __future__ import annotations

import secrets
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

from core import run_all
from core.ai_review import run_ai_review
from core.final_export import FINAL_REVIEW_FILENAME, export_final_review_result
from core.llm_client import get_llm_config, test_llm_connection
from core.pipeline import OUTPUT_FILENAMES
from core.review_store import (
    MANUAL_REVIEW_RESULTS,
    append_review_log,
    compute_review_stats,
    create_task_meta,
    generate_review_items_from_excel,
    init_review_state,
    load_review_items,
    load_review_state,
    load_task_meta,
    review_badge,
    review_sort_key,
    update_review_item,
    update_task_meta,
)

APP_ROOT = Path(__file__).resolve().parent
TEMP_ROOT = APP_ROOT / "temp"
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
SOURCE_FILTERS = ["全部", "完全同名匹配对比结果", "vcu-hcu 同名匹配"]
FIELD_FILTERS = ["全部", "信号长度", "精度", "偏移量", "物理最小值", "物理最大值", "单位", "信号值描述", "未解析"]
AI_FILTERS = ["全部", "疑似一致", "疑似错别字", "疑似语义相近", "真实差异", "无法判断", "不适用", "未启用"]
REVIEW_SOURCE_FILTERS = ["全部", "需人工优先确认", "系统默认保留", "人工已修改"]
MANUAL_STATUS_FILTERS = ["全部", "待人工确认", "已有结论", "人工已修改", "系统默认结论", *MANUAL_REVIEW_RESULTS]


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _new_task_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _save_uploads(files: Iterable, target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for uploaded_file in files:
        filename = _safe_filename(uploaded_file.name)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型：{uploaded_file.name}，仅支持 .xlsx / .xlsm")
        target = target_dir / filename
        target.write_bytes(uploaded_file.getbuffer())
        saved.append(target)
    return saved


def _task_dir(task_id: str) -> Path:
    return TEMP_ROOT / task_id


def _review_dir(task_dir: Path) -> Path:
    return task_dir / "review"


def _output_dir(task_dir: Path) -> Path:
    return task_dir / "output"


def _scan_tasks() -> list[str]:
    if not TEMP_ROOT.exists():
        return []
    tasks = [p.name for p in TEMP_ROOT.iterdir() if p.is_dir() and (p / "task_meta.json").exists()]
    return sorted(tasks, reverse=True)


def _zip_outputs(task_dir: Path, zip_path: Path) -> Path:
    output_dir = _output_dir(task_dir)
    review_dir = _review_dir(task_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename in OUTPUT_FILENAMES.values():
            path = output_dir / filename
            if path.exists():
                zf.write(path, arcname=f"output/{filename}")
        final_path = output_dir / FINAL_REVIEW_FILENAME
        if final_path.exists():
            zf.write(final_path, arcname=f"output/{FINAL_REVIEW_FILENAME}")
        for path, arcname in [
            (task_dir / "task_meta.json", "task_meta.json"),
            (review_dir / "review_items.json", "review/review_items.json"),
            (review_dir / "review_state.json", "review/review_state.json"),
            (review_dir / "review_log.jsonl", "review/review_log.jsonl"),
        ]:
            if path.exists():
                zf.write(path, arcname=arcname)
    return zip_path


def _show_downloads(task_dir: Path) -> None:
    output_dir = _output_dir(task_dir)
    st.subheader("结果文件下载")
    for filename in OUTPUT_FILENAMES.values():
        path = output_dir / filename
        if not path.exists():
            st.warning(f"未找到输出文件：{filename}")
            continue
        with path.open("rb") as fh:
            st.download_button(
                label=f"下载 {filename}",
                data=fh.read(),
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download-{task_dir.name}-{filename}",
            )

    final_path = output_dir / FINAL_REVIEW_FILENAME
    if final_path.exists():
        with final_path.open("rb") as fh:
            st.download_button(
                label=f"下载 {FINAL_REVIEW_FILENAME}",
                data=fh.read(),
                file_name=FINAL_REVIEW_FILENAME,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download-final-{task_dir.name}",
            )

    zip_path = _zip_outputs(task_dir, task_dir / "全部结果文件.zip")
    with zip_path.open("rb") as fh:
        st.download_button(
            label="下载全部结果 zip",
            data=fh.read(),
            file_name="matrix_signal_diff_results.zip",
            mime="application/zip",
            key=f"download-zip-{task_dir.name}",
        )


def _restore_task(task_id: str) -> None:
    task_id = task_id.strip()
    if not task_id:
        st.warning("请输入或选择 task_id")
        return
    task_dir = _task_dir(task_id)
    meta = load_task_meta(task_dir)
    if not meta:
        st.error(f"未找到任务：{task_id}")
        return
    if not (_review_dir(task_dir) / "review_items.json").exists():
        st.error(f"任务 {task_id} 缺少 review_items.json，无法恢复人工审核。")
        return
    st.session_state["current_task_id"] = task_id
    st.success(f"已恢复任务：{task_id}")


def _show_history_loader() -> None:
    with st.sidebar:
        st.header("继续历史任务")
        tasks = _scan_tasks()
        selected = st.selectbox("最近任务", options=[""] + tasks, format_func=lambda x: x or "请选择", key="history_task_select")
        manual = st.text_input("手动输入 task_id", key="manual_task_id")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("恢复所选任务", disabled=not selected):
                _restore_task(selected)
        with col2:
            if st.button("恢复输入任务", disabled=not manual.strip()):
                _restore_task(manual)


def _show_ai_config() -> tuple[bool, int]:
    enable_ai_review = st.checkbox("启用 AI 辅助复核", value=False)
    st.caption("AI 仅对“信号值描述/单位”等文本差异进行辅助判断，不会修改原始差异结果；所有差异仍需人工审核。")
    llm_config = get_llm_config()
    max_ai_review_items = st.number_input(
        "本次最多 AI 复核条数",
        min_value=0,
        max_value=10000,
        value=max(llm_config.max_review_items, 0),
        step=1,
    )
    if "llm_connection_status" not in st.session_state:
        st.session_state["llm_connection_status"] = {"status": "not_tested", "message": "未测试"}

    status_map = {"not_tested": "未测试", "disabled": "未测试", "success": "连接成功", "failed": "连接失败"}
    connection_status = st.session_state["llm_connection_status"]
    with st.expander("AI 配置状态", expanded=False):
        st.write(f"LLM_ENABLED 当前值：{'true' if llm_config.enabled else 'false'}")
        st.write(f"LLM_BASE_URL 是否已配置：{'已配置' if llm_config.base_url else '未配置'}")
        st.write(f"LLM_MODEL 当前值：{llm_config.model or '未配置'}")
        st.write(f"LLM_API_KEY 是否已配置：{'已配置' if llm_config.api_key else '未配置'}")
        if st.button("测试大模型连接"):
            result = test_llm_connection()
            st.session_state["llm_connection_status"] = result
            connection_status = result
            if result.get("status") == "success":
                st.success(f"连接成功：model={result.get('model')}，耗时={result.get('elapsed_seconds')} 秒")
            elif result.get("status") == "disabled":
                st.warning(result.get("message", "AI辅助复核未启用"))
            else:
                st.error(result.get("error", "连接失败"))
        st.write(f"当前连接状态：{status_map.get(connection_status.get('status'), '未测试')}")
        if connection_status.get("message"):
            st.caption(connection_status["message"])
        if connection_status.get("error"):
            st.error(connection_status["error"])
    return enable_ai_review, int(max_ai_review_items)


def _show_new_task(enable_ai_review: bool, max_ai_review_items: int) -> None:
    st.header("新建任务")
    files_40 = st.file_uploader("上传 4.0 矩阵文件（支持多个 .xlsx / .xlsm）", type=["xlsx", "xlsm"], accept_multiple_files=True)
    files_51 = st.file_uploader("上传 5.1 矩阵文件（支持多个 .xlsx / .xlsm）", type=["xlsx", "xlsm"], accept_multiple_files=True)

    disabled = not files_40 or not files_51
    if disabled:
        st.info("请分别上传至少 1 个 4.0 和 5.1 矩阵 Excel 文件后开始识别。")

    if st.button("开始识别", type="primary", disabled=disabled):
        task_id = _new_task_id()
        task_dir = _task_dir(task_id)
        input_40_dir = task_dir / "input" / "4.0"
        input_51_dir = task_dir / "input" / "5.1"
        output_dir = _output_dir(task_dir)
        review_dir = _review_dir(task_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)

        progress = st.progress(0)
        status = st.empty()
        log_box = st.container()
        create_task_meta(task_dir, task_id, status="created")

        try:
            status.write("已创建临时任务目录，正在保存上传文件...")
            saved_40 = _save_uploads(files_40, input_40_dir)
            saved_51 = _save_uploads(files_51, input_51_dir)
            update_task_meta(task_dir, input_40_count=len(saved_40), input_51_count=len(saved_51), status="running")
            progress.progress(15)
            st.success(f"已保存上传文件：4.0={len(saved_40)} 个，5.1={len(saved_51)} 个。task_id：{task_id}")

            status.write("正在执行 01/02/03 legacy 流程，生成全量、去重和最终差异结果...")
            pipeline_result = run_all(input_40_dir, input_51_dir, output_dir)
            progress.progress(75)

            status.write("正在生成 AI辅助复核与人工审核明细 sheet...")
            ai_progress = st.progress(0)
            ai_status = st.empty()
            ai_log = st.empty()

            def update_ai_progress(payload):
                stage = payload.get("stage", "")
                total = int(payload.get("total") or 0)
                current = int(payload.get("current") or 0)
                completed = int(payload.get("completed") or 0)
                failed = int(payload.get("failed") or 0)
                signal_name = payload.get("signal_name") or ""
                ai_status.write(stage)
                if total > 0:
                    ai_progress.progress(min(current / total, 1.0))
                    ai_log.write(f"当前 AI 复核进度：第 {current} / {total} 条；当前信号名：{signal_name}；已完成：{completed}；已失败：{failed}")
                elif stage:
                    ai_log.write(stage)

            compare_file = pipeline_result["files"]["compare"]
            ai_stats = run_ai_review(compare_file, enable_ai=enable_ai_review, max_ai_review_items=max_ai_review_items, progress_callback=update_ai_progress)
            ai_progress.progress(1.0)
            update_task_meta(task_dir, status="ai_review_done")

            status.write("正在生成网页端人工审核数据...")
            review_items = generate_review_items_from_excel(compare_file, review_dir)
            init_review_state(review_dir, task_id, review_items)
            update_task_meta(task_dir, status="reviewing")
            progress.progress(100)
            status.write("处理完成，已进入人工审核工作台。")
            st.session_state["current_task_id"] = task_id

            st.subheader("结果统计")
            stats_df = pd.DataFrame([{"指标": key, "数量": value} for key, value in pipeline_result["statistics"].items()])
            st.dataframe(stats_df, hide_index=True, use_container_width=True)
            st.subheader("AI/人工审核明细统计")
            st.dataframe(pd.DataFrame([{"指标": key, "数量": value} for key, value in ai_stats.items() if key != "warnings"]), hide_index=True, use_container_width=True)
            st.success(f"已生成 review_items.json，共 {len(review_items)} 条字段级差异。")
            for warning in ai_stats.get("warnings", []):
                st.warning(warning)
            with st.expander("查看执行日志"):
                for result in pipeline_result["logs"]:
                    st.markdown(f"**{result['script']}**（returncode={result['returncode']}）")
                    st.code(result.get("stdout") or "<empty stdout>", language="text")
                    if result.get("stderr"):
                        st.code(result["stderr"], language="text")
        except Exception as exc:  # noqa: BLE001
            update_task_meta(task_dir, status="failed", error=str(exc))
            progress.progress(100)
            status.write("处理失败。")
            st.error(str(exc))
            with log_box.expander("错误详情（可复制）", expanded=True):
                st.code(traceback.format_exc(), language="text")


def _filter_items(items: list[dict], state: dict, filters: dict[str, str]) -> list[dict]:
    state_items = state.get("items", {})
    out = []
    for item in items:
        review = state_items.get(item.get("item_id"), {})
        result = review.get("manual_review_result", "")
        source = review.get("review_source", "")
        badge = review_badge(item, review)
        if filters["source"] != "全部" and item.get("source_sheet") != filters["source"]:
            continue
        if filters["field"] != "全部" and item.get("diff_field") != filters["field"]:
            continue
        if filters["ai"] != "全部" and item.get("ai_judgement") != filters["ai"]:
            continue
        if filters["review_source"] != "全部" and badge != filters["review_source"]:
            continue
        manual = filters["manual"]
        if manual == "待人工确认" and review.get("reviewed"):
            continue
        if manual == "已有结论" and not result:
            continue
        if manual == "人工已修改" and source != "manual":
            continue
        if manual == "系统默认结论" and source != "system_default":
            continue
        if manual in MANUAL_REVIEW_RESULTS and result != manual:
            continue
        out.append(item)
    return out


def _show_review_workspace() -> None:
    task_id = st.session_state.get("current_task_id")
    if not task_id:
        return
    task_dir = _task_dir(task_id)
    review_dir = _review_dir(task_dir)
    meta = load_task_meta(task_dir)
    items = load_review_items(review_dir)
    state = init_review_state(review_dir, task_id, items)
    if not items:
        st.warning(f"当前任务 {task_id} 没有可审核数据。")
        return

    st.header("人工审核工作台")
    st.caption(f"当前 task_id：`{task_id}`")
    if meta:
        st.caption(f"任务状态：{meta.get('status', '')}；创建时间：{meta.get('created_at', '')}")

    stats = compute_review_stats(items, state)
    labels = [
        ("总审核项数", "total"),
        ("需人工优先确认", "priority_review"),
        ("系统默认保留", "system_default_keep"),
        ("人工已修改", "manual_modified"),
        ("待人工确认", "pending_manual"),
        ("最终保留差异", "confirmed_real_diff"),
        ("确认可忽略", "ignored"),
        ("确认错别字", "typo"),
        ("确认语义一致", "semantic_same"),
        ("存疑待确认", "uncertain"),
    ]
    for row_start in range(0, len(labels), 5):
        cols = st.columns(5)
        for col, (label, key) in zip(cols, labels[row_start : row_start + 5]):
            col.metric(label, stats.get(key, 0))
    st.caption(f"最后保存时间：{stats.get('updated_at') or '尚未保存人工审核'}")

    st.subheader("筛选")
    c1, c2, c3, c4 = st.columns(4)
    c5, _, _, _ = st.columns(4)
    filters = {
        "source": c1.selectbox("来源Sheet", SOURCE_FILTERS),
        "field": c2.selectbox("差异字段", FIELD_FILTERS),
        "ai": c3.selectbox("AI判断结果", AI_FILTERS),
        "review_source": c4.selectbox("审核来源", REVIEW_SOURCE_FILTERS),
        "manual": c5.selectbox("人工审核状态", MANUAL_STATUS_FILTERS),
    }
    filtered = _filter_items(items, state, filters)
    state_items_for_sort = state.get("items", {})
    filtered = sorted(filtered, key=lambda item: review_sort_key(item, state_items_for_sort.get(item.get("item_id"), {})))
    st.write(f"当前筛选结果：{len(filtered)} 条")

    if not filtered:
        _show_final_export(task_dir, review_dir)
        _show_downloads(task_dir)
        return

    page_size = st.number_input("每页显示条数", min_value=1, max_value=50, value=20, step=1)
    total_pages = max((len(filtered) - 1) // int(page_size) + 1, 1)
    page = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1, key=f"review-page-input-{task_id}")
    start = (int(page) - 1) * int(page_size)
    page_items = filtered[start : start + int(page_size)]

    st.subheader("审核操作")
    state_items = state.get("items", {})
    for offset, item in enumerate(page_items, start=start + 1):
        item_id = item["item_id"]
        review = state_items.get(item_id, {})
        title = f"{offset}. {item.get('signal_40') or '<空>'} ⇄ {item.get('signal_51') or '<空>'}｜{item.get('diff_field')}"
        preferred_item = st.session_state.get(f"expand-item-{task_id}")
        with st.expander(title, expanded=(preferred_item == item_id) or (not preferred_item and offset == start + 1)):
            left, right = st.columns(2)
            left.markdown("**4.0 内容**")
            left.code(item.get("value_40", "") or "<空>", language="text")
            right.markdown("**5.1 内容**")
            right.code(item.get("value_51", "") or "<空>", language="text")
            st.write(f"来源Sheet：{item.get('source_sheet', '')}")
            badge = review_badge(item, review)
            st.write(f"AI判断结果：{item.get('ai_judgement', '')}；差异类型：{item.get('difference_type', '')}；置信度：{item.get('confidence', '')}")
            st.write(f"AI建议处理方式：{item.get('ai_suggested_action', '')}")
            st.write(f"系统默认结论：{review.get('default_review_result') or '无'}")
            st.write(f"当前最终结论：{review.get('manual_review_result') or '待人工确认'}")
            st.write(f"审核来源：{badge}")
            if badge == "系统默认保留":
                st.success("该条已由系统默认保留为真实差异，人工可修改。")
            elif badge == "需人工优先确认":
                st.warning("AI判断该差异可能可忽略，请优先人工确认。")
            st.info(item.get("ai_reason", "") or review.get("default_reason") or "无 AI 理由")
            with st.expander("查看原始差异点list", expanded=False):
                st.code(item.get("original_diff_list", ""), language="text")

            current_result = review.get("manual_review_result", "")
            options = [""] + MANUAL_REVIEW_RESULTS
            selected_idx = options.index(current_result) if current_result in options else 0
            manual_result = st.selectbox("人工审核结果", options, index=selected_idx, format_func=lambda x: x or "未审核", key=f"manual-result-{task_id}-{item_id}")
            manual_note = st.text_area("人工备注", value=review.get("manual_note", ""), key=f"manual-note-{task_id}-{item_id}")
            b1, b2 = st.columns(2)
            if b1.button("保存当前审核", key=f"save-{task_id}-{item_id}"):
                update_review_item(review_dir, task_id, item_id, manual_result, manual_note)
                update_task_meta(task_dir, status="reviewing")
                st.success("已保存到 review_state.json")
                st.rerun()
            if b2.button("保存并下一条", key=f"save-next-{task_id}-{item_id}"):
                update_review_item(review_dir, task_id, item_id, manual_result, manual_note)
                update_task_meta(task_dir, status="reviewing")
                next_index = min(offset, len(filtered) - 1)
                if filtered:
                    st.session_state[f"expand-item-{task_id}"] = filtered[next_index]["item_id"]
                st.success("已保存到 review_state.json")
                st.rerun()

    _show_batch_actions(task_dir, review_dir, task_id, filtered, state)
    _show_final_export(task_dir, review_dir)
    _show_downloads(task_dir)


def _show_batch_actions(task_dir: Path, review_dir: Path, task_id: str, filtered: list[dict], state: dict) -> None:
    with st.expander("批量操作（可选，谨慎使用）", expanded=False):
        st.warning("批量操作只会作用于当前筛选结果中的“需人工优先确认”且未审核记录，不会批量改动系统默认保留的真实差异。")
        result = st.selectbox("批量设置结果", ["确认可忽略", "存疑待确认"], key=f"batch-result-{task_id}")
        confirm = st.checkbox("我确认要批量更新当前筛选结果中的未审核记录", key=f"batch-confirm-{task_id}")
        if st.button("执行批量更新", disabled=not confirm, key=f"batch-apply-{task_id}"):
            state_items = state.get("items", {})
            count = 0
            for item in filtered:
                item_id = item["item_id"]
                review = state_items.get(item_id, {})
                if review.get("reviewed") or review_badge(item, review) != "需人工优先确认":
                    continue
                update_review_item(review_dir, task_id, item_id, result, "批量操作生成")
                count += 1
            try:
                append_review_log(review_dir, {"task_id": task_id, "action": "batch_update", "manual_review_result": result, "count": count})
            except OSError:
                st.warning("批量审核日志写入失败，但审核状态已保存。")
            update_task_meta(task_dir, status="reviewing")
            st.success(f"已批量更新 {count} 条。")
            st.rerun()


def _show_final_export(task_dir: Path, review_dir: Path) -> None:
    st.subheader("生成最终结果")
    final_path = _output_dir(task_dir) / FINAL_REVIEW_FILENAME
    if st.button("生成最终审核结果", type="primary", key=f"export-final-{task_dir.name}"):
        stats = export_final_review_result(review_dir / "review_items.json", review_dir / "review_state.json", final_path)
        update_task_meta(task_dir, status="final_exported")
        st.success(f"已生成：{final_path}")
        st.dataframe(pd.DataFrame([{"指标": k, "数量": v} for k, v in stats.items()]), hide_index=True, use_container_width=True)
    if final_path.exists():
        st.info(f"最终审核结果文件已存在：{final_path}")


def main() -> None:
    st.set_page_config(page_title="EEA 4.0/5.1 矩阵同一信号差异识别工具", layout="wide")
    st.title("EEA 4.0/5.1 矩阵同一信号差异识别工具")
    st.caption("本地 Streamlit Demo：封装 legacy 脚本流程；AI 复核仅作为人工审核参考，最终以人工审核结果为准。")
    _show_history_loader()
    enable_ai_review, max_ai_review_items = _show_ai_config()
    _show_new_task(enable_ai_review, max_ai_review_items)
    _show_review_workspace()


if __name__ == "__main__":
    main()
