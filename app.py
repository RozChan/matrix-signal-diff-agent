from __future__ import annotations

import html
import os
import secrets
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from core import run_all
from core.ai_review import run_ai_review
from core.final_export import FINAL_REVIEW_FILENAME, export_final_review_result
from core.llm_client import get_llm_config, test_llm_connection
from core.pipeline import OUTPUT_FILENAMES
from core.result_notifier import build_results_zip
from core.result_access import allowed_result_files, ensure_result_access, result_token_valid
from core.notification_router import notify_result_ready
from core.admin_tasks import admin_system_status, admin_token_valid, cancel_admin_task, create_admin_full_compare, list_admin_tasks, retry_admin_confluence, safe_task_dir
from core.task_progress import allowed_admin_actions, beijing_time, build_task_progress, choose_default_task, status_label, trigger_label
from core.review_table import pending_review_count
from ui.admin_progress import render_live_task_progress
from ui.review_table import render_compact_review, render_review_stats, render_system_differences
from core.review_store import (
    acquire_review_lock,
    begin_final_generation,
    create_task_meta,
    generate_review_items_from_excel,
    init_review_state,
    load_review_items,
    load_task_meta,
    heartbeat_review_lock,
    is_signal_level_item,
    ReviewLockError,
    update_task_meta,
)

APP_ROOT = Path(__file__).resolve().parent
TEMP_ROOT = Path(os.getenv("TASK_ROOT_DIR", str(APP_ROOT / "temp"))).expanduser().resolve()
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
REVIEW_LOCK_READY_STATUSES = {"awaiting_review", "reviewing"}
APP_TITLE = "EEA4.0 & EEA5.1信号对比人工确认"


def _display_text(value: object) -> str:
    text = "" if value is None else str(value)
    if text.strip().lower() in {"nan", "none"}:
        return "<空>"
    return text if text else "<空>"


def _session_id(task_id: str) -> str:
    key = f"review-session-id-{task_id}"
    if key not in st.session_state:
        st.session_state[key] = secrets.token_urlsafe(16)
    return str(st.session_state[key])


def _parse_iso_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_review_completed(meta: dict) -> bool:
    return bool(meta.get("review_completed")) or meta.get("status") in {"final_exported", "delivered"}


def _lock_is_active_for_other(meta: dict, session_id: str) -> bool:
    expires = _parse_iso_datetime(meta.get("review_lock_expires_at"))
    return bool(
        meta.get("review_lock_status") == "locked"
        and meta.get("review_session_id")
        and meta.get("review_session_id") != session_id
        and expires
        and expires > datetime.now(timezone.utc)
    )


def _review_lock_state_key(task_id: str, name: str) -> str:
    return f"review-lock-{name}-{task_id}"


def _show_once(task_id: str, name: str, message: str, level: str = "success") -> None:
    key = _review_lock_state_key(task_id, f"shown-{name}")
    if st.session_state.get(key):
        return
    st.session_state[key] = True
    getattr(st, level)(message)


def _auto_acquire_review_lock(task_dir: Path, task_id: str, session_id: str, meta: dict) -> tuple[bool, dict]:
    completed = _is_review_completed(meta)
    if completed or meta.get("status") not in REVIEW_LOCK_READY_STATUSES:
        st.session_state[_review_lock_state_key(task_id, "acquired")] = False
        return False, meta

    if meta.get("review_lock_status") == "locked" and meta.get("review_session_id") == session_id:
        try:
            meta = heartbeat_review_lock(task_dir, session_id)
            st.session_state[_review_lock_state_key(task_id, "acquired")] = True
            return True, meta
        except ReviewLockError:
            st.session_state[_review_lock_state_key(task_id, "acquired")] = False
            return False, load_task_meta(task_dir)

    attempted_key = _review_lock_state_key(task_id, "auto-acquire-attempted")
    if not st.session_state.get(attempted_key):
        st.session_state[attempted_key] = True
        lock_was_expired = bool(
            meta.get("review_lock_status") == "locked"
            and meta.get("review_session_id")
            and meta.get("review_session_id") != session_id
            and not _lock_is_active_for_other(meta, session_id)
        )
        if not _lock_is_active_for_other(meta, session_id):
            try:
                meta = acquire_review_lock(task_dir, session_id, owner=session_id)
                st.session_state[_review_lock_state_key(task_id, "acquired")] = True
                if lock_was_expired:
                    _show_once(task_id, "expired-auto-acquired", "原审核锁已过期，当前会话已自动接管审核。", "success")
                else:
                    _show_once(task_id, "auto-acquired", "已自动进入审核模式。", "success")
                return True, meta
            except ReviewLockError:
                st.session_state[_review_lock_state_key(task_id, "acquired")] = False
                return False, load_task_meta(task_dir)

    meta = load_task_meta(task_dir)
    is_editor = bool(meta.get("review_lock_status") == "locked" and meta.get("review_session_id") == session_id and not _is_review_completed(meta))
    st.session_state[_review_lock_state_key(task_id, "acquired")] = is_editor
    return is_editor, meta


def _show_review_lock_panel(task_dir: Path, task_id: str, _state: dict | None = None) -> tuple[bool, str, dict]:
    session_id = _session_id(task_id)
    meta = load_task_meta(task_dir)
    is_editor, meta = _auto_acquire_review_lock(task_dir, task_id, session_id, meta)
    completed = _is_review_completed(meta)
    mode = "编辑模式" if is_editor else "只读模式"
    owner = meta.get("review_owner") or "无"
    st.info(
        "｜".join(
            [
                f"任务状态：{meta.get('status', '')}",
                f"当前模式：{mode}",
                f"当前审核人：{owner}",
                f"锁定时间：{beijing_time(meta.get('review_locked_at'))}",
                f"最近活动：{beijing_time(meta.get('review_lock_last_active_at'))}",
            ]
        )
    )
    if completed:
        st.success("该任务已完成审核，当前仅支持查看。")
        return False, session_id, meta
    if meta.get("status") not in REVIEW_LOCK_READY_STATUSES:
        st.warning(f"任务当前状态为 {meta.get('status', '') or '未知'}，尚未进入人工审核阶段，当前为只读模式。")
        return False, session_id, meta
    if is_editor:
        return True, session_id, meta

    if _lock_is_active_for_other(meta, session_id):
        st.warning(f"该任务正在由{owner}审核，当前为只读模式。最近活动时间：{beijing_time(meta.get('review_lock_last_active_at'))}")
        takeover = st.checkbox("我确认要接管当前审核", key=f"takeover-confirm-{task_id}")
        if st.button("接管审核", disabled=not takeover, key=f"takeover-review-{task_id}"):
            try:
                acquire_review_lock(task_dir, session_id, owner=session_id, takeover=True)
                st.warning("已接管审核编辑锁。")
                st.rerun()
            except ReviewLockError as exc:
                st.error(str(exc))
    else:
        st.warning("当前页面为只读模式；审核锁暂不可用，请刷新页面或稍后重试。")
    return False, session_id, meta


def _review_badge_style(badge: str) -> tuple[str, str, str]:
    if badge == "需人工优先确认":
        return "#fff4cc", "#f59f00", "🟠"
    if badge == "系统默认保留":
        return "#e6f4ea", "#2f9e44", "🟢"
    if badge == "人工已修改":
        return "#e7f0ff", "#1c7ed6", "🔵"
    return "#f1f3f5", "#868e96", "⚪"


def _render_review_card_header(title: str, badge: str, judgement: str, final_result: str) -> None:
    bg, border, icon = _review_badge_style(badge)
    st.markdown(
        f"""
        <div style="background:{bg}; border-left:8px solid {border}; padding:10px 14px;
                    border-radius:8px; margin:10px 0 4px 0; font-weight:600;">
            {icon} {html.escape(title)}
            <span style="float:right; font-weight:500;">{html.escape(badge)} ｜ AI：{html.escape(judgement or '无')} ｜ 结论：{html.escape(final_result or '待人工确认')}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
            (task_dir / "bot" / "received_files.json", "bot/received_files.json"),
            (task_dir / "bot" / "bot_events.jsonl", "bot/bot_events.jsonl"),
            (task_dir / "bot" / "delivery_state.json", "bot/delivery_state.json"),
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


def _show_history_loader(hide_history: bool = False) -> None:
    if hide_history:
        return
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


def _show_ai_config() -> bool:
    enable_ai_review = st.checkbox("启用 AI 辅助复核", value=True)
    st.caption("AI 仅对“信号值描述/单位”等文本差异进行辅助判断，不会修改原始差异结果；所有差异仍需人工审核。")
    llm_config = get_llm_config()
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
    return enable_ai_review


def _show_new_task(enable_ai_review: bool) -> None:
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
            update_task_meta(task_dir, source="local", input_40_count=len(saved_40), input_51_count=len(saved_51), status="running")
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
                    field_total = int(payload.get("field_total") or 0)
                    ai_log.write(f"信号级 AI 复核进度：第 {current} / {total} 个信号；当前信号名：{signal_name}；已完成信号数：{completed}；已失败信号数：{failed}；涉及差异字段总数：{field_total}")
                elif stage:
                    ai_log.write(stage)

            compare_file = pipeline_result["files"]["compare"]
            ai_stats = run_ai_review(compare_file, enable_ai=enable_ai_review, progress_callback=update_ai_progress)
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
            st.success(f"已生成 review_items.json，共 {len(review_items)} 条信号级差异。")
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


def _show_review_workspace() -> None:
    task_id = st.session_state.get("current_task_id")
    if not task_id:
        return
    task_dir = _task_dir(task_id)
    review_dir = _review_dir(task_dir)
    meta = load_task_meta(task_dir)
    items = load_review_items(review_dir)
    if items and not all(is_signal_level_item(item) for item in items):
        st.warning("当前任务使用旧版字段级审核数据，建议重新运行任务生成信号级审核数据。")
        _show_downloads(task_dir)
        return
    state = init_review_state(review_dir, task_id, items)
    if not items:
        st.warning(f"当前任务 {task_id} 没有可审核数据。")
        return

    can_edit, session_id, meta = _show_review_lock_panel(task_dir, task_id)
    state, dirty_count = render_compact_review(task_dir, review_dir, task_id, items, state, can_edit=can_edit, session_id=session_id, display_text=_display_text)
    pending_count = pending_review_count(state)
    _show_final_export(task_dir, review_dir, session_id=session_id, can_edit=can_edit, dirty_count=dirty_count, pending_count=pending_count)
    render_system_differences(items)
    render_review_stats(items, state)
    _show_downloads(task_dir)


def _show_final_export(task_dir: Path, review_dir: Path, *, session_id: str, can_edit: bool, dirty_count: int = 0, pending_count: int = 0) -> None:
    st.subheader("生成最终结果")
    final_path = _output_dir(task_dir) / FINAL_REVIEW_FILENAME
    meta = load_task_meta(task_dir)
    already_done = bool(meta.get("review_completed")) or meta.get("status") in {"final_exported", "delivered"}
    delivery_active = meta.get("result_delivery_status") in {"sending", "delivered"}
    disabled = (not can_edit) or already_done or delivery_active or dirty_count > 0 or pending_count > 0
    if dirty_count:
        st.warning(f"还有 {dirty_count} 条未保存修改，请先保存。")
    if pending_count:
        st.warning(f"还有 {pending_count} 条待人工确认，完成后才能提交最终结果。")
    if st.button("完成审核并生成最终结果", type="primary", key=f"export-final-{task_dir.name}", disabled=disabled):
        try:
            begin_final_generation(task_dir, session_id)
        except ReviewLockError as exc:
            st.error(str(exc))
            return
        try:
            stats = export_final_review_result(review_dir / "review_items.json", review_dir / "review_state.json", final_path)
            meta = load_task_meta(task_dir)
            updates = {"status": "final_exported", "final_generation_status": "done"}
            if meta.get("notify_type") == "feishu_custom_bot":
                build_results_zip(task_dir)
                updates["result_delivery_status"] = "web_ready"
            elif meta.get("source") in {"feishu", "feishu_confluence", "auto_full_compare"}:
                updates["result_delivery_status"] = "pending"
            update_task_meta(task_dir, **updates)
            if meta.get("notify_type") == "feishu_custom_bot":
                ensure_result_access(task_dir)
                notify_result_ready(task_dir)
            st.success(f"已生成：{final_path}")
            st.dataframe(pd.DataFrame([{"指标": k, "数量": v} for k, v in stats.items()]), hide_index=True, use_container_width=True)
        except Exception as exc:  # noqa: BLE001
            update_task_meta(task_dir, review_completed=False, review_completed_at="", final_generation_status="failed", error=str(exc))
            st.error(f"生成最终结果失败：{exc}")
            raise
    if already_done or delivery_active:
        st.info("该任务已完成审核或正在生成/发送结果，不能重复提交。")
    elif not can_edit:
        st.caption("只读模式不能生成最终结果，请先获取审核编辑锁。")
    if final_path.exists():
        st.info(f"最终审核结果文件已存在：{final_path}")


def _show_result_page(task_id: str, token: str) -> None:
    try:
        tdir = safe_task_dir(task_id)
    except (ValueError, FileNotFoundError):
        st.error("任务不存在或task_id无效。")
        return
    if not result_token_valid(tdir, token):
        st.error("结果下载链接无效或无权访问。")
        return
    meta = load_task_meta(tdir)
    st.title("信号矩阵全量对比结果下载")
    st.write(f"任务编号：{task_id}")
    st.write(f"任务状态：{meta.get('status', '')}")
    if meta.get("status") in {"cancelled", "failed"}:
        st.warning(f"任务未正常完成：{meta.get('error') or meta.get('current_stage') or ''}")
        return
    if meta.get("status") not in {"final_exported", "delivered"}:
        st.info("最终结果尚未生成。")
        return
    files = allowed_result_files(tdir)
    if not files:
        st.warning("没有可下载的结果文件。")
        return
    for path in files:
        mime = "application/zip" if path.suffix.lower() == ".zip" else ("application/json" if path.suffix.lower() == ".json" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button(f"下载 {path.name}", path.read_bytes(), file_name=path.name, mime=mime, key=f"result-{task_id}-{path.name}")


def _show_admin_page() -> None:
    st.title("管理员任务管理")
    if os.getenv("ADMIN_PAGE_ENABLED", "false").lower() != "true":
        st.error("管理员页面未启用。")
        return
    if not st.session_state.get("admin_authenticated"):
        token = st.text_input("管理员访问Token", type="password")
        if st.button("登录管理员页面"):
            if admin_token_valid(token):
                st.session_state["admin_authenticated"] = True
                st.rerun()
            else:
                st.error("管理员Token错误。")
        return
    status = admin_system_status()
    st.subheader("系统状态")
    st.json(status)
    st.subheader("手动创建全量任务")
    st.write(f"4.0父页面：{os.getenv('FULL_COMPARE_40_PARENT_URL', '') or '<未配置>'}")
    st.write(f"5.1父页面：{os.getenv('FULL_COMPARE_51_PARENT_URL', '') or '<未配置>'}")
    st.write(f"最新版本选择：{os.getenv('CONFLUENCE_PARENT_SELECT_LATEST_VERSION', 'true')}")
    st.write(f"严格模式：{os.getenv('CONFLUENCE_LATEST_VERSION_STRICT', 'true')}｜通知方式：飞书群自定义机器人")
    confirm = st.checkbox("确认启动一次4.0与5.1全量信号对比")
    if "admin_create_operation_id" not in st.session_state:
        st.session_state["admin_create_operation_id"] = f"admin:{secrets.token_urlsafe(18)}"
    if st.button("创建自动全量任务", disabled=not confirm):
        try:
            result = create_admin_full_compare(st.session_state["admin_create_operation_id"])
            st.session_state["admin_create_operation_id"] = f"admin:{secrets.token_urlsafe(18)}"
            st.session_state["admin_selected_task_id"] = result.task_id
            st.session_state["admin_selector_version"] = int(st.session_state.get("admin_selector_version", 0)) + 1
            st.success(f"任务已创建：{result.task_id}")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    rows = list_admin_tasks()
    preferred = str(st.session_state.get("admin_selected_task_id") or st.query_params.get("admin_task_id", ""))
    selected_default = choose_default_task(rows, preferred)
    labels = {
        row["task_id"]: f"{row['task_id']}｜{status_label(row['status'])}｜{trigger_label(row['trigger_source'])}｜{row['created_at_display']}"
        for row in rows
    }
    task_ids = list(labels)
    selected = st.selectbox(
        "查看任务",
        task_ids,
        index=task_ids.index(selected_default) if selected_default in task_ids else 0,
        format_func=lambda task_id: labels[task_id],
        key=f"admin-task-selector-{int(st.session_state.get('admin_selector_version', 0))}",
    ) if task_ids else ""
    if selected:
        st.session_state["admin_selected_task_id"] = selected
        st.query_params["admin_task_id"] = selected
        initial = build_task_progress(safe_task_dir(selected))
        snapshot = render_live_task_progress(selected, initial["active"])

    st.subheader("最近任务列表")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if selected:
        row = next(item for item in rows if item["task_id"] == selected)
        actions = allowed_admin_actions(row["status"])
        st.subheader("当前任务操作")
        with st.expander("查看任务详情", expanded=False):
            st.json(snapshot)
        if "cancel" in actions:
            confirm_cancel = st.checkbox("确认取消当前运行任务", key=f"confirm-cancel-{selected}")
            if st.button("取消任务", key=f"admin-cancel-{selected}", disabled=not confirm_cancel):
                st.success("任务已取消。" if cancel_admin_task(selected) else "任务已取消或当前状态不允许取消。")
                st.rerun()
        if "retry_confluence" in actions and st.button("重试失败的Confluence来源", key=f"admin-retry-{selected}"):
            st.success(f"已启动 {retry_admin_confluence(selected)} 个失败来源重试。")
            st.rerun()
        if "recreate" in actions:
            st.caption("已失败或取消的旧worker不会恢复；重新创建将生成新的task_id。")
            if st.button("重新创建同类全量任务", key=f"admin-recreate-{selected}"):
                result = create_admin_full_compare(f"admin:{secrets.token_urlsafe(18)}")
                st.session_state["admin_selected_task_id"] = result.task_id
                st.session_state["admin_selector_version"] = int(st.session_state.get("admin_selector_version", 0)) + 1
                st.rerun()
        if row.get("review_url"):
            st.link_button("进入人工审核", row["review_url"])
        if row.get("result_url"):
            st.link_button("进入结果下载页", row["result_url"])


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    view = str(st.query_params.get("view", ""))
    if not view and not st.query_params.get("task_id") and not st.query_params.get("token"):
        st.query_params["view"] = "admin"
        st.rerun()
    if view == "admin":
        _show_admin_page()
        return
    if view == "results":
        _show_result_page(str(st.query_params.get("task_id", "")), str(st.query_params.get("result_token", "")))
        return
    query_task_id = st.query_params.get("task_id", "")
    query_token = st.query_params.get("token", "")
    feishu_link_mode = bool(query_task_id or query_token)
    if feishu_link_mode:
        st.title(APP_TITLE)
        task_dir = _task_dir(str(query_task_id))
        meta = load_task_meta(task_dir)
        if not meta or not query_token or meta.get("review_token") != query_token:
            st.error("无权访问或审核链接无效。")
            return
        st.session_state["current_task_id"] = str(query_task_id)
    else:
        st.title(APP_TITLE)
        st.caption("本地 Streamlit Demo：封装 legacy 脚本流程；AI 复核仅作为人工审核参考，最终以人工审核结果为准。")
    _show_history_loader(hide_history=feishu_link_mode)
    if not feishu_link_mode:
        enable_ai_review = _show_ai_config()
        _show_new_task(enable_ai_review)
    _show_review_workspace()


if __name__ == "__main__":
    main()
