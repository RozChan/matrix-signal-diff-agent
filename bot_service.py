"""Feishu bot entrypoint for matrix signal diff tasks.

This service adds a Feishu/lark-cli entry to the existing local Streamlit
workflow. It keeps message handling short, persists task state under
``temp/<task_id>``, and starts long-running matrix processing in a separate
worker subprocess.
"""

from __future__ import annotations

import json
import logging
import os
import re
import hashlib
import subprocess
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from core.bot_task_store import (
    atomic_write_json,
    append_bot_event,
    bot_dir,
    clear_active_session,
    create_upload_session,
    get_active_task_id,
    get_task_root,
    record_received_file,
    scan_task_metas,
    set_active_task_id,
    task_dir,
)
from core.confluence_client import ConfluenceClient, ConfluenceError
from core.confluence_source_parser import parse_confluence_sources
from core.confluence_task_store import add_sources, load_confluence_sources, set_worker_state, task_lock, update_source
from core.file_intake import detect_version, sanitize_filename, store_received_file, validate_extension
from core.full_compare_task import (
    FullCompareBusyError,
    FullCompareConfigurationError,
    create_full_matrix_compare_task,
)
from core.lark_cli_client import LarkCliClient
from core.progress_card import sync_task_progress_card
from core.result_notifier import notify_review_ready, notify_task_failed, scan_and_notify
from core.review_store import load_task_meta, update_task_meta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
log = logging.getLogger("matrix-feishu-bot")

START_COMMANDS = {"开始信号矩阵对比", "开始矩阵对比", "信号矩阵对比", "新建任务"}
PROCESS_COMMANDS = {"开始处理", "开始识别", "开始"}
RETRY_CONFLUENCE_COMMANDS = {"重试Confluence下载", "重试confluence下载", "重试下载"}
IGNORE_FAILED_CONFLUENCE_COMMANDS = {"忽略失败来源并开始处理", "忽略失败并开始处理"}
FULL_COMPARE_COMMANDS = {"创建自动全量任务", "全量信号对比", "开始全量信号对比", "执行全量信号自动对比"}
ADD_40_COMMANDS = {"添加4.0文件", "上传4.0", "4.0"}
ADD_51_COMMANDS = {"添加5.1文件", "上传5.1", "5.1"}
_PROCESSED: set[str] = set()
_PROCESSED_LOCK = threading.Lock()
_MAX_PROCESSED = 2000
_VERSION_HINTS: dict[str, str] = {}
_LAST_STAGE_NOTICE: dict[str, str] = {}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def _allowed_user(sender_id: str) -> bool:
    allowed = [part.strip() for part in os.getenv("FEISHU_ALLOWED_OPEN_IDS", "").split(",") if part.strip()]
    return not allowed or sender_id in allowed


def _event_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flattened and standard Feishu event payload variants."""

    candidates = [event]
    for path in [("event",), ("data",), ("data", "event")]:
        value: Any = event
        for key in path:
            value = value.get(key, {}) if isinstance(value, dict) else {}
        if isinstance(value, dict) and value not in candidates:
            candidates.append(value)
    return candidates


def _dedupe(message_id: str) -> bool:
    with _PROCESSED_LOCK:
        if message_id in _PROCESSED:
            return False
        _PROCESSED.add(message_id)
        if len(_PROCESSED) > _MAX_PROCESSED:
            for old in list(_PROCESSED)[: _MAX_PROCESSED // 2]:
                _PROCESSED.discard(old)
    return True


def _extract_text(event: dict[str, Any]) -> str:
    content: Any = ""
    for candidate in _event_candidates(event):
        message = candidate.get("message") if isinstance(candidate.get("message"), dict) else {}
        content = candidate.get("content") or message.get("content") or ""
        if content:
            break
    if isinstance(content, str):
        try:
            data = json.loads(content)
            return str(data.get("text") or data.get("content") or content).strip()
        except json.JSONDecodeError:
            return content.strip()
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "").strip()
    return ""


def _extract_file_info(event: dict[str, Any]) -> dict[str, str] | None:
    content = event.get("content", {})
    data: dict[str, Any]
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}
    elif isinstance(content, dict):
        data = content
    else:
        data = {}
    file_key = str(data.get("file_key") or data.get("key") or data.get("file_token") or data.get("fileKey") or "")
    file_name = str(data.get("file_name") or data.get("name") or data.get("fileName") or data.get("title") or "")
    if not file_key:
        m = re.search(r"(file|box|tmp|om)_[-_A-Za-z0-9]+", json.dumps(data, ensure_ascii=False))
        file_key = m.group(0) if m else ""
    if not file_name:
        for value in data.values():
            if isinstance(value, str) and Path(value).suffix.lower() in {".xlsx", ".xlsm", ".zip"}:
                file_name = value
                break
    if file_key or file_name:
        return {"file_key": file_key, "file_name": sanitize_filename(file_name or f"{file_key}.bin")}
    return None


def _sender_id(event: dict[str, Any]) -> str:
    for candidate in _event_candidates(event):
        sender = candidate.get("sender") if isinstance(candidate.get("sender"), dict) else {}
        sender_ids = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
        direct = candidate.get("sender_id")
        if isinstance(direct, dict):
            direct = direct.get("open_id")
        value = direct or sender_ids.get("open_id") or sender.get("open_id")
        if value:
            return str(value).strip()
    return ""


def _chat_id(event: dict[str, Any]) -> str:
    for candidate in _event_candidates(event):
        message = candidate.get("message") if isinstance(candidate.get("message"), dict) else {}
        value = candidate.get("chat_id") or message.get("chat_id")
        if value:
            return str(value)
    return ""


def _message_id(event: dict[str, Any]) -> str:
    for candidate in _event_candidates(event):
        message = candidate.get("message") if isinstance(candidate.get("message"), dict) else {}
        value = candidate.get("message_id") or message.get("message_id")
        if value:
            return str(value)
    return ""


def _message_type(event: dict[str, Any]) -> str:
    for candidate in _event_candidates(event):
        message = candidate.get("message") if isinstance(candidate.get("message"), dict) else {}
        value = candidate.get("message_type") or message.get("message_type")
        if value:
            return str(value)
    return ""


def _ensure_session(sender_id: str, chat_id: str, message_id: str, client: LarkCliClient) -> tuple[str, Path]:
    task_id = get_active_task_id(sender_id)
    if not task_id:
        session = create_upload_session(sender_id, chat_id, message_id)
        task_id = session["task_id"]
        client.reply_text(message_id, f"已创建任务：{task_id}\n请上传文件，文件名需包含 4.0 或 5.1。支持 xlsx、xlsm、zip。上传完成后发送“开始处理”。")
    return task_id, task_dir(task_id)


def _count_input_files(tdir: Path) -> tuple[int, int]:
    return (len(list((tdir / "input" / "4.0").glob("*.xls*"))), len(list((tdir / "input" / "5.1").glob("*.xls*"))))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_confluence_task_size(tdir: Path) -> None:
    limit = int(os.getenv("CONFLUENCE_MAX_TASK_SIZE_MB", "1000")) * 1024 * 1024
    total = sum(path.stat().st_size for path in (tdir / "input").rglob("*") if path.is_file())
    if total > limit:
        raise ValueError(f"Confluence下载文件总大小超过限制：{round(total / 1024 / 1024, 2)}MB")


def _merge_version_artifact(tdir: Path, name: str, version: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one version's selection/manifest without racing its peer thread."""

    path = tdir / name
    with task_lock(tdir):
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        versions = dict(existing.get("versions") or {})
        versions[version] = payload
        artifact = {"task_id": tdir.name, "versions": versions, "updated_at": _utc_now_iso()}
        atomic_write_json(path, artifact)
        return artifact


def _existing_input_hashes(tdir: Path, version: str) -> set[str]:
    hashes: set[str] = set()
    for path in (tdir / "input" / version).glob("*.xls*"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.add(digest)
    return hashes


def _safe_update_task_meta(tdir: Path, **updates: Any) -> bool:
    try:
        update_task_meta(tdir, **updates)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("task_meta状态保存失败：%s", exc)
        try:
            update_source(tdir, updates.get("source_url", ""), state_persistence_error=str(exc))
        except Exception:  # noqa: BLE001
            pass
        return False


def _handle_start(event: dict[str, Any], client: LarkCliClient) -> None:
    sender = _sender_id(event)
    message_id = _message_id(event)
    session = create_upload_session(sender, _chat_id(event), message_id)
    client.reply_text(message_id, f"任务编号：{session['task_id']}\n请上传文件，文件名需包含 4.0 或 5.1。支持 xlsx、xlsm、zip。上传完成后发送“开始处理”。")


def _handle_full_compare_command(event: dict[str, Any], client: LarkCliClient, command: str) -> None:
    message_id = _message_id(event)
    sender = _sender_id(event)
    if not _env_bool("FULL_COMPARE_COMMAND_ENABLED", "false"):
        client.reply_text(message_id, "自动全量信号对比功能尚未启用。")
        return
    try:
        result = create_full_matrix_compare_task(
            trigger_source="feishu_command",
            trigger_id=message_id,
            requested_by=sender,
            notify_type="user",
            notify_target=sender,
            trigger_metadata={"trigger_user_open_id": sender, "trigger_command": command, "feishu_chat_id": _chat_id(event)},
        )
    except FullCompareBusyError as exc:
        client.reply_text(message_id, f"当前已有自动全量信号对比任务正在执行。\n\n任务编号：{exc.task_id}\n当前阶段：{exc.stage}\n当前进度：{exc.progress}%")
        return
    except FullCompareConfigurationError as exc:
        client.reply_text(message_id, f"自动全量任务前置校验失败：{exc}")
        return
    tdir = result.task_dir
    set_active_task_id(sender, result.task_id, _chat_id(event))
    sync_task_progress_card(tdir, client, force=not result.duplicate)
    if result.duplicate:
        client.reply_text(message_id, f"该触发消息已创建过自动全量任务。\n任务编号：{result.task_id}")
        return
    client.reply_text(
        message_id,
        "已创建自动全量信号对比任务。\n\n"
        f"任务编号：{result.task_id}\n"
        "4.0来源：26R1通讯矩阵父页面\n"
        "5.1来源：26R2通讯矩阵父页面\n\n"
        "系统将自动识别各模块最新版本、下载有效信号矩阵并执行差异识别，完成后发送人工审核入口。",
    )
    for source in result.sources:
        threading.Thread(target=_download_confluence_source, args=(result.task_id, tdir, dict(source), client, sender), daemon=True).start()


def _handle_file(event: dict[str, Any], client: LarkCliClient) -> None:
    if not _env_bool("BOT_ALLOW_FILE_UPLOAD", "false"):
        client.reply_text(_message_id(event), "当前任务暂时使用Confluence网址作为输入，请发送4.0和5.1的Confluence页面地址。")
        return
    sender = _sender_id(event)
    message_id = _message_id(event)
    task_id, tdir = _ensure_session(sender, _chat_id(event), message_id, client)
    file_info = _extract_file_info(event)
    if not file_info:
        client.reply_text(message_id, "暂未从该飞书事件中解析到文件信息，请在公司工作站验证 file 消息事件字段。")
        return
    file_name = file_info["file_name"]
    version = detect_version(file_name, _VERSION_HINTS.get(sender, ""))
    try:
        validate_extension(file_name)
    except ValueError as exc:
        client.reply_text(message_id, str(exc))
        return
    if not version:
        client.reply_text(message_id, "无法识别文件版本。请确保文件名包含 4.0 或 5.1，或先发送“添加4.0文件/添加5.1文件”。")
        return
    tmp_path = Path(tempfile.mkdtemp()) / sanitize_filename(file_name)
    downloaded = client.download_message_file(message_id, file_info.get("file_key", ""), tmp_path, file_type="file")
    if not downloaded:
        client.reply_text(message_id, "文件下载失败：当前 lark-cli 文件下载命令需在公司工作站验证。")
        return
    try:
        stored = store_received_file(downloaded, tdir, file_name, version)
        records = record_received_file(tdir, {"message_id": message_id, "file_key": file_info.get("file_key", ""), "file_name": file_name, "version": version, "stored_files": [str(p.name) for p in stored]})
        count40 = sum(1 for item in records if item.get("version") == "4.0")
        count51 = sum(1 for item in records if item.get("version") == "5.1")
        client.reply_text(message_id, f"已接收：\n版本：{version}\n文件：{file_name}\n当前4.0文件：{count40}个\n当前5.1文件：{count51}个\n上传完成后发送“开始处理”。")
    except Exception as exc:  # noqa: BLE001
        client.reply_text(message_id, f"文件处理失败：{exc}")


def _start_worker(task_id: str, enable_ai: bool = True) -> subprocess.Popen:
    args = [sys.executable, "-m", "core.task_worker", "--task-id", task_id]
    if not enable_ai:
        args.append("--disable-ai")
    log.info("start worker: %s", " ".join(args))
    return subprocess.Popen(args, cwd=Path(__file__).resolve().parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _monitor_worker_completion(task_id: str, tdir: Path, process: subprocess.Popen, client: LarkCliClient) -> None:
    return_code = process.wait()
    meta = load_task_meta(tdir)
    if return_code != 0 and meta.get("status") not in {"failed", "awaiting_review", "final_exported", "delivered"}:
        update_task_meta(tdir, status="failed", current_stage="失败", stage_progress=100, error=f"task_worker退出码非0：{return_code}")
        meta = load_task_meta(tdir)
    sync_task_progress_card(tdir, client, force=True)
    if meta.get("status") == "awaiting_review":
        notify_review_ready(client, tdir, meta)
    elif meta.get("status") == "failed":
        notify_task_failed(client, tdir, meta)
    log.info("worker finished task_id=%s return_code=%s status=%s", task_id, return_code, meta.get("status"))


def _handle_process(event: dict[str, Any], client: LarkCliClient) -> None:
    sender = _sender_id(event)
    message_id = _message_id(event)
    task_id = get_active_task_id(sender)
    if not task_id:
        client.reply_text(message_id, "未找到当前上传会话，请先发送“开始信号矩阵对比”。")
        return
    tdir = task_dir(task_id)
    if _start_ready_task(task_id, tdir, client, sender, manual_ignore_failed=False):
        clear_active_session(sender)
        client.reply_text(message_id, f"任务 {task_id} 已启动后台处理。处理完成后我会发送人工审核链接。")


def _failed_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in data.get("sources", []) if item.get("status") == "failed"]


def _format_failed_sources(failed: list[dict[str, Any]]) -> str:
    lines = []
    for index, item in enumerate(failed, start=1):
        errors = item.get("errors") or []
        reason = "; ".join(str(error) for error in errors) if errors else "未知错误"
        lines.append(f"{index}. 版本：{item.get('version', '')}\n   网址：{item.get('url', '')}\n   原因：{reason}")
    return "\n".join(lines)


def _notify_failed_sources(task_id: str, client: LarkCliClient, user_id: str, failed: list[dict[str, Any]]) -> None:
    client.send_text(
        user_id,
        f"任务 {task_id} 存在 Confluence 来源下载失败，已停止自动开始处理。\n\n"
        f"失败来源：\n{_format_failed_sources(failed)}\n\n"
        "如需重试，请回复“重试Confluence下载”。\n"
        "如确认忽略这些失败来源并使用已下载Excel继续，请回复“忽略失败来源并开始处理”。",
    )


def _failure_notice_fingerprint(task_id: str, failed: list[dict[str, Any]]) -> str:
    parts = [task_id]
    for item in sorted(failed, key=lambda src: str(src.get("url", ""))):
        errors = ";".join(str(error) for error in (item.get("errors") or []))
        parts.append(f"{item.get('url', '')}|{errors[:300]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _notify_failed_sources_once(task_id: str, tdir: Path, client: LarkCliClient, user_id: str, failed: list[dict[str, Any]]) -> None:
    if not failed:
        return
    fingerprint = _failure_notice_fingerprint(task_id, failed)
    with task_lock(tdir):
        data = load_confluence_sources(tdir, task_id)
        sources = data.get("sources", [])
        if any(item.get("status") not in {"completed", "failed"} for item in sources):
            return
        if data.get("confluence_failure_notice_fingerprint") == fingerprint and data.get("confluence_failure_notice_status") in {"sending", "sent"}:
            return
        data["confluence_failure_notice_status"] = "sending"
        data["confluence_failure_notice_fingerprint"] = fingerprint
        data["confluence_failure_notice_error"] = ""
        set_worker_state(tdir, **data)
    try:
        _notify_failed_sources(task_id, client, user_id, failed)
    except Exception as exc:  # noqa: BLE001
        with task_lock(tdir):
            set_worker_state(tdir, confluence_failure_notice_status="failed", confluence_failure_notice_error=str(exc))
        return
    with task_lock(tdir):
        set_worker_state(tdir, confluence_failure_notice_status="sent", confluence_failure_notice_sent_at=_utc_now_iso(), confluence_failure_notice_error="")


def _start_ready_task(task_id: str, tdir: Path, client: LarkCliClient, user_id: str, *, manual_ignore_failed: bool = False) -> bool:
    with task_lock(tdir):
        meta = load_task_meta(tdir)
        if meta.get("status") in {"running", "ai_review_done", "awaiting_review", "final_exported", "delivered"}:
            return False
        count40, count51 = _count_input_files(tdir)
        update_task_meta(tdir, input_40_count=count40, input_51_count=count51)
        if count40 < 1 or count51 < 1:
            client.send_text(user_id, f"暂不能开始处理：4.0和5.1均需至少1个有效Excel。当前：4.0={count40}，5.1={count51}")
            return False
        data = load_confluence_sources(tdir, task_id)
        sources = data.get("sources", [])
        failed = _failed_sources(data)
        if failed and not manual_ignore_failed:
            _notify_failed_sources_once(task_id, tdir, client, user_id, failed)
            return False
        unfinished = [item for item in sources if item.get("status") not in {"completed", "failed"}]
        if unfinished or not data.get("sources_registration_complete", True):
            client.send_text(user_id, "Confluence来源仍在下载、扫描或登记中，请稍后再试。")
            return False
        if meta.get("source") == "auto_full_compare" and any(not item.get("selection_complete") for item in sources):
            return False
        if data.get("worker_starting") or data.get("worker_started"):
            return False
        set_worker_state(tdir, worker_starting=True, worker_started=False, worker_error="")
    try:
        process = _start_worker(task_id, enable_ai=True)
    except Exception as exc:  # noqa: BLE001
        set_worker_state(tdir, worker_starting=False, worker_started=False, worker_error=str(exc))
        update_task_meta(tdir, error=str(exc))
        client.send_text(user_id, f"任务 {task_id} 启动后台处理失败：{exc}")
        return False
    set_worker_state(tdir, worker_starting=False, worker_started=True, worker_started_at=_utc_now_iso(), worker_error="")
    update_task_meta(tdir, status="running", current_stage="开始信号矩阵对比", stage_progress=1)
    sync_task_progress_card(tdir, client, force=True)
    if hasattr(process, "wait"):
        threading.Thread(target=_monitor_worker_completion, args=(task_id, tdir, process, client), daemon=True).start()
    if manual_ignore_failed and failed:
        client.send_text(user_id, f"已按你的确认忽略 {len(failed)} 个失败来源，正在使用已下载Excel开始信号矩阵差异识别。")
    return True


def _source_summary(data: dict[str, Any], tdir: Path) -> tuple[int, int, int, int]:
    sources = data.get("sources", [])
    source40 = sum(1 for item in sources if item.get("version") == "4.0")
    source51 = sum(1 for item in sources if item.get("version") == "5.1")
    count40, count51 = _count_input_files(tdir)
    return source40, count40, source51, count51


def _maybe_auto_start(task_id: str, tdir: Path, client: LarkCliClient, user_id: str) -> None:
    should_notify_failed = False
    should_notify_complete = False
    can_auto_start = False
    summary = (0, 0, 0, 0)
    with task_lock(tdir):
        data = load_confluence_sources(tdir, task_id)
        sources = data.get("sources", [])
        if not sources or not data.get("sources_registration_complete", True):
            return
        failed = _failed_sources(data)
        unfinished = [item for item in sources if item.get("status") not in {"completed", "failed"}]
        if unfinished:
            return
        summary = _source_summary(data, tdir)
        count40, count51 = summary[1], summary[3]
        update_task_meta(tdir, input_40_count=count40, input_51_count=count51)
        should_notify_failed = bool(failed)
        all_completed = all(item.get("status") == "completed" for item in sources)
        selections_complete = all(not item.get("select_latest_version") or item.get("selection_complete") for item in sources)
        if not data.get("all_sources_reported"):
            data["all_sources_reported"] = True
            set_worker_state(tdir, **data)
            should_notify_complete = True
        can_auto_start = (
            all_completed
            and selections_complete
            and not failed
            and count40 >= 1
            and count51 >= 1
            and _env_bool("BOT_AUTO_START_WHEN_BOTH_READY", "true")
            and not data.get("worker_starting")
            and not data.get("worker_started")
            and load_task_meta(tdir).get("status") not in {"running", "ai_review_done", "awaiting_review", "final_exported", "delivered"}
        )
    if should_notify_complete:
        sync_task_progress_card(tdir, client, force=True)
    if should_notify_failed:
        _notify_failed_sources_once(task_id, tdir, client, user_id, _failed_sources(load_confluence_sources(tdir, task_id)))
        return
    if can_auto_start:
        _start_ready_task(task_id, tdir, client, user_id, manual_ignore_failed=False)
    elif should_notify_complete and not _env_bool("BOT_AUTO_START_WHEN_BOTH_READY", "true"):
        sync_task_progress_card(tdir, client, force=True)


def _download_confluence_source(task_id: str, tdir: Path, source: dict[str, Any], client: LarkCliClient, user_id: str) -> None:
    url = source["url"]
    version = source["version"]
    mode = source["mode"]
    try:
        update_source(tdir, url, status="scanning")
        _safe_update_task_meta(tdir, status="created", current_stage=f"解析{version} Confluence页面", stage_progress=3)
        sync_task_progress_card(tdir, client, force=True)
        with ConfluenceClient() as confluence:
            page_id = confluence.resolve_page_id(url)
            update_source(tdir, url, resolved_page_id=page_id, status="scanning")
            selection: dict[str, Any] = {}
            if mode == "children_recursive" and source.get("select_latest_version"):
                _safe_update_task_meta(tdir, status="downloading", current_stage="识别模块和版本", stage_progress=4)
                attachments, selection = confluence.discover_latest_excel_attachments(
                    page_id,
                    url,
                    strict=bool(source.get("latest_version_strict", True)),
                )
                page_count = sum(1 for _ in selection.get("selections", [])) + len(selection.get("unclassified_pages", []))
                selected_artifact = _merge_version_artifact(tdir, "selected_pages.json", version, selection)
                all_selections = list(selected_artifact.get("versions", {}).values())
                module_count = sum(len(item.get("selections", [])) for item in all_selections)
                selected_count = sum(sum(1 for page in item.get("selections", []) if page.get("selected_page_id")) for item in all_selections)
                skipped_count = sum(sum(len(page.get("skipped_pages", [])) for page in item.get("selections", [])) for item in all_selections)
                unrecognized_count = sum(len(item.get("unclassified_pages", [])) + len(item.get("warnings", [])) for item in all_selections)
                _safe_update_task_meta(
                    tdir,
                    current_stage="选择最新版本",
                    stage_progress=6,
                    full_compare_module_count=module_count,
                    full_compare_selected_page_count=selected_count,
                    full_compare_skipped_history_count=skipped_count,
                    full_compare_unrecognized_count=unrecognized_count,
                )
                if selection.get("strict_blocked") or selection.get("page_tree_errors"):
                    reasons = []
                    if selection.get("strict_blocked"):
                        reasons.append("严格模式下存在版本选择歧义")
                    if selection.get("page_tree_errors"):
                        reasons.append("页面树存在未能读取的节点")
                    update_source(tdir, url, status="failed", errors=reasons, selection_complete=True, selection_warnings=selection.get("warnings", []))
                    sync_task_progress_card(tdir, client, force=True)
                    _maybe_auto_start(task_id, tdir, client, user_id)
                    return
            else:
                attachments = confluence.discover_excel_attachments(page_id, mode, url)
                page_count = 1 if mode == "current_page" else len(confluence.list_descendant_pages(page_id))
            update_source(tdir, url, status="downloading", page_count=page_count, page_scanned=page_count, attachment_count=len(attachments), downloaded_count=0)
            _safe_update_task_meta(tdir, current_stage=f"下载{version} Confluence矩阵", confluence_page_total=page_count, confluence_page_scanned=page_count, confluence_attachment_total=len(attachments))
            sync_task_progress_card(tdir, client)
            if not attachments:
                update_source(tdir, url, status="failed", errors=["该页面未发现 .xlsx/.xlsm 附件"], page_count=page_count, attachment_count=0, downloaded_count=0)
                sync_task_progress_card(tdir, client, force=True)
                _maybe_auto_start(task_id, tdir, client, user_id)
                return
            target_dir = tdir / "input" / version
            downloaded = []
            seen_ids: set[str] = set()
            known_hashes = _existing_input_hashes(tdir, version)
            manifest_records: list[dict[str, Any]] = []
            for index, attachment in enumerate(attachments, start=1):
                aid = attachment.get("attachment_id", "")
                if aid and aid in seen_ids:
                    continue
                seen_ids.add(aid)
                local_path = confluence.download_attachment(attachment, target_dir)
                _ensure_confluence_task_size(tdir)
                sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
                if sha256 in known_hashes:
                    local_path.unlink(missing_ok=True)
                    attachment["skip_reason"] = "内容SHA-256重复"
                    continue
                known_hashes.add(sha256)
                attachment["local_path"] = str(local_path)
                attachment["version"] = version
                attachment["sha256"] = sha256
                attachment["downloaded_at"] = _utc_now_iso()
                downloaded.append(attachment)
                manifest_records.append({
                    "task_id": task_id,
                    "version": version,
                    "root_page_url": url,
                    "page_id": attachment.get("page_id", ""),
                    "page_title": attachment.get("page_title", ""),
                    "module_key": attachment.get("module_key", ""),
                    "module_title": attachment.get("module_title", ""),
                    "selected_version": attachment.get("selected_version", ""),
                    "attachment_id": attachment.get("attachment_id", ""),
                    "attachment_name": attachment.get("file_name", ""),
                    "attachment_version": attachment.get("attachment_version", 0),
                    "attachment_updated_at": attachment.get("attachment_updated_at", ""),
                    "file_size": local_path.stat().st_size,
                    "local_path": str(local_path),
                    "sha256": sha256,
                    "downloaded_at": attachment["downloaded_at"],
                })
                update_source(tdir, url, downloaded_count=len(downloaded), attachments=downloaded)
                _safe_update_task_meta(tdir, confluence_downloaded_count=len(downloaded), current_signal="")
                sync_task_progress_card(tdir, client)
            if source.get("select_latest_version"):
                _merge_version_artifact(tdir, "input_manifest.json", version, {"root_page_url": url, "files": manifest_records, "excluded_attachments": selection.get("excluded_attachments", [])})
            try:
                update_source(tdir, url, status="completed", selection_complete=True, page_count=page_count, page_scanned=page_count, attachment_count=len(attachments), downloaded_count=len(downloaded), attachments=downloaded, errors=[])
            except Exception as exc:  # noqa: BLE001
                client.send_text(user_id, f"任务 {task_id} 状态保存失败，已停止自动启动，请人工检查。\n网址：{url}\n错误：{exc}")
                _safe_update_task_meta(tdir, status="interrupted", current_stage="任务状态保存失败", error=str(exc))
                return
            sync_task_progress_card(tdir, client, force=True)
            _maybe_auto_start(task_id, tdir, client, user_id)
    except ConfluenceError as exc:
        update_source(tdir, url, status="failed", errors=[str(exc)])
        _safe_update_task_meta(tdir, current_stage="Confluence下载失败", error=str(exc))
        sync_task_progress_card(tdir, client, force=True)
        _maybe_auto_start(task_id, tdir, client, user_id)
    except Exception as exc:  # noqa: BLE001
        update_source(tdir, url, status="failed", errors=[str(exc)])
        _safe_update_task_meta(tdir, current_stage="Confluence下载失败", error=str(exc))
        sync_task_progress_card(tdir, client, force=True)
        _maybe_auto_start(task_id, tdir, client, user_id)


def _handle_confluence_message(event: dict[str, Any], client: LarkCliClient, text: str) -> bool:
    parsed = parse_confluence_sources(text)
    if not parsed.sources and not parsed.unresolved_urls:
        return False
    sender = _sender_id(event)
    message_id = _message_id(event)
    task_id = get_active_task_id(sender)
    if not task_id:
        session = create_upload_session(sender, _chat_id(event), message_id)
        task_id = session["task_id"]
    tdir = task_dir(task_id)
    meta = load_task_meta(tdir)
    if meta.get("status") in {"running", "ai_review_done", "awaiting_review", "final_exported", "delivered"}:
        client.reply_text(message_id, "当前任务已开始处理，不能继续追加Confluence来源。请发送“开始信号矩阵对比”新建任务。")
        return True
    if parsed.unresolved_urls:
        client.reply_text(message_id, "无法确定以下网址属于4.0还是5.1，请回复明确格式，例如“4.0页面 <URL>”或“5.1父页面 <URL>”：\n" + "\n".join(parsed.unresolved_urls))
        return True
    auto_start = _env_bool("BOT_AUTO_START_WHEN_BOTH_READY", "true")
    parent_select_latest = _env_bool("CONFLUENCE_PARENT_SELECT_LATEST_VERSION", "true")
    latest_strict = _env_bool("CONFLUENCE_LATEST_VERSION_STRICT", "true")
    sources = [{
        "version": src.version,
        "mode": src.mode,
        "url": src.url,
        "status": "pending",
        "page_count": 0,
        "attachment_count": 0,
        "downloaded_count": 0,
        "select_latest_version": bool(src.mode == "children_recursive" and parent_select_latest),
        "latest_version_strict": latest_strict,
        "errors": [],
    } for src in parsed.sources]
    added = add_sources(tdir, sources, auto_start=auto_start)
    update_task_meta(tdir, source="feishu_confluence", input_mode="confluence_url", status="created", current_stage="已识别Confluence来源", stage_progress=1)
    sync_task_progress_card(tdir, client, force=True)
    page40 = sum(1 for item in added if item.get("version") == "4.0" and item.get("mode") == "current_page")
    page51 = sum(1 for item in added if item.get("version") == "5.1" and item.get("mode") == "current_page")
    client.reply_text(message_id, f"已识别{len(added)}个Confluence来源：\n- 4.0当前页面：{page40}个\n- 5.1当前页面：{page51}个")
    for source in added:
        threading.Thread(target=_download_confluence_source, args=(task_id, tdir, source, client, sender), daemon=True).start()
    return True



def _handle_retry_confluence(event: dict[str, Any], client: LarkCliClient) -> None:
    sender = _sender_id(event)
    message_id = _message_id(event)
    task_id = get_active_task_id(sender)
    if not task_id:
        client.reply_text(message_id, "未找到当前Confluence任务，请先发送Confluence页面地址。")
        return
    tdir = task_dir(task_id)
    with task_lock(tdir):
        data = load_confluence_sources(tdir, task_id)
        failed = _failed_sources(data)
        for item in failed:
            update_source(tdir, item.get("url", ""), status="pending", errors=[], downloaded_count=0)
        set_worker_state(tdir, confluence_failure_notice_status="pending", confluence_failure_notice_fingerprint="", confluence_failure_notice_error="")
    if not failed:
        client.reply_text(message_id, "当前任务没有失败的Confluence来源需要重试。")
        return
    client.reply_text(message_id, f"开始重试 {len(failed)} 个失败的Confluence来源。")
    sync_task_progress_card(tdir, client, force=True)
    for item in failed:
        source = {**item, "status": "pending", "errors": [], "downloaded_count": 0}
        threading.Thread(target=_download_confluence_source, args=(task_id, tdir, source, client, sender), daemon=True).start()


def _handle_ignore_failed_and_start(event: dict[str, Any], client: LarkCliClient) -> None:
    sender = _sender_id(event)
    message_id = _message_id(event)
    task_id = get_active_task_id(sender)
    if not task_id:
        client.reply_text(message_id, "未找到当前Confluence任务，请先发送Confluence页面地址。")
        return
    tdir = task_dir(task_id)
    data = load_confluence_sources(tdir, task_id)
    failed = _failed_sources(data)
    if not failed:
        client.reply_text(message_id, "当前任务没有失败来源，将按正常条件尝试开始处理。")
    if _start_ready_task(task_id, tdir, client, sender, manual_ignore_failed=True):
        clear_active_session(sender)
        client.reply_text(message_id, f"任务 {task_id} 已启动后台处理。处理完成后我会发送人工审核链接。")

def handle_event(event: dict[str, Any], client: LarkCliClient) -> None:
    message_id = _message_id(event)
    try:
        if not message_id:
            return
        sender = _sender_id(event)
        text = _extract_text(event)
        if text in FULL_COMPARE_COMMANDS:
            _handle_full_compare_command(event, client, text)
            return
        if not _dedupe(message_id):
            return
        if not _allowed_user(sender):
            client.reply_text(message_id, "当前飞书用户不在允许名单中，无法发起任务。")
            return
        msg_type = _message_type(event)
        task_id = get_active_task_id(sender)
        if task_id:
            append_bot_event(task_dir(task_id), {"message_id": message_id, "sender_id": sender, "message_type": msg_type, "text": text})
        if text in START_COMMANDS:
            _handle_start(event, client)
            return
        if text in ADD_40_COMMANDS:
            _VERSION_HINTS[sender] = "4.0"
            client.reply_text(message_id, "下一次上传的文件将按 4.0 归类。")
            return
        if text in ADD_51_COMMANDS:
            _VERSION_HINTS[sender] = "5.1"
            client.reply_text(message_id, "下一次上传的文件将按 5.1 归类。")
            return
        if text in RETRY_CONFLUENCE_COMMANDS:
            _handle_retry_confluence(event, client)
            return
        if text in IGNORE_FAILED_CONFLUENCE_COMMANDS:
            _handle_ignore_failed_and_start(event, client)
            return
        if text in PROCESS_COMMANDS:
            _handle_process(event, client)
            return
        if text and _handle_confluence_message(event, client, text):
            return
        if msg_type in {"file", "media"} or _extract_file_info(event):
            _handle_file(event, client)
            return
        if text:
            client.reply_text(message_id, "请发送“开始信号矩阵对比”创建任务，或上传矩阵文件后发送“开始处理”。")
    except Exception as exc:  # noqa: BLE001
        log.error("处理飞书消息失败：%s\n%s", exc, traceback.format_exc())
        if message_id:
            client.reply_text(message_id, f"本条消息处理失败：{exc}\n请检查格式后重试，或联系维护人员查看日志。")


def recover_on_start(client: LarkCliClient) -> None:
    for tdir, meta in scan_task_metas():
        if meta.get("source") not in {"feishu", "feishu_confluence", "auto_full_compare"}:
            continue
        if meta.get("status") == "running":
            update_task_meta(tdir, status="interrupted", error="bot_service 重启后无法确认后台 worker 是否仍在运行。")
        if meta.get("status") in {"created", "running", "ai_review_done", "awaiting_review", "failed"}:
            sync_task_progress_card(tdir, client)
        if meta.get("source") == "auto_full_compare" and meta.get("status") in {"created", "downloading"}:
            data = load_confluence_sources(tdir, str(meta.get("task_id") or tdir.name))
            resumed = []
            for source in data.get("sources", []):
                if source.get("status") in {"pending", "scanning", "downloading"}:
                    update_source(tdir, source.get("url", ""), status="pending", errors=[])
                    resumed.append({**source, "status": "pending", "errors": []})
            target = str(meta.get("feishu_sender_id") or meta.get("notify_target") or "")
            for source in resumed:
                threading.Thread(target=_download_confluence_source, args=(tdir.name, tdir, source, client, target), daemon=True).start()
            if not resumed:
                _maybe_auto_start(tdir.name, tdir, client, target)
    scan_and_notify(client)


def monitor_tasks_loop(client: LarkCliClient, interval_seconds: int = 15) -> None:
    import time

    while True:
        for tdir, meta in scan_task_metas():
            if meta.get("source") not in {"feishu", "feishu_confluence", "auto_full_compare"}:
                continue
            if meta.get("status") in {"created", "running", "ai_review_done", "awaiting_review", "failed", "final_exported"}:
                sync_task_progress_card(tdir, client)
        scan_and_notify(client)
        time.sleep(interval_seconds)


def consume_events(client: LarkCliClient) -> None:
    log.info("开始监听飞书消息事件：<LARK_CLI> event consume im.message.receive_v1 --as bot")
    proc = client.open_event_consumer()

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                log.info("[event-consume] %s", line.strip())

    threading.Thread(target=read_stderr, daemon=True).start()
    if proc.stdin:
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
        except OSError:
            pass
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.warning("无法解析事件：%s", line[:200])
            continue
        threading.Thread(target=handle_event, args=(event, client), daemon=True).start()


def main() -> int:
    if os.getenv("FEISHU_BOT_ENABLED", "false").lower() != "true":
        print("FEISHU_BOT_ENABLED=false，飞书机器人未启用。如需启动，请在 .env 或环境变量中设置 FEISHU_BOT_ENABLED=true。", file=sys.stderr)
        return 2
    cli_path = os.getenv("LARK_CLI_PATH", "").strip()
    if not cli_path:
        print("缺少 LARK_CLI_PATH，请配置 lark-cli 可执行文件路径。", file=sys.stderr)
        return 2
    client = LarkCliClient(cli_path)
    get_task_root().mkdir(parents=True, exist_ok=True)
    recover_on_start(client)
    threading.Thread(target=monitor_tasks_loop, args=(client,), daemon=True).start()
    consume_events(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
