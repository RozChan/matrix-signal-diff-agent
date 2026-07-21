"""Route lifecycle notifications without coupling task sources to transports."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .feishu_custom_bot import FeishuCustomBotClient
from .result_access import allowed_result_files, ensure_result_access
from .review_store import load_task_meta, update_task_meta
from .task_lock import get_task_lock


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def notification_channel(meta: dict[str, Any]) -> str:
    kind = str(meta.get("notify_type") or "")
    if kind == "feishu_custom_bot":
        return "feishu_custom_bot"
    if kind in {"none", ""} and not (meta.get("feishu_chat_id") or meta.get("feishu_sender_id")):
        return "none"
    return "enterprise_app"


def _admin_url() -> str:
    base = os.getenv("REVIEW_BASE_URL", "http://localhost:8501").rstrip("/")
    return f"{base}/?{urlencode({'view': 'admin'})}"


def _custom_once(task_dir: Path, event: str, title: str, markdown: str, *, button_text: str = "", button_url: str = "", client: FeishuCustomBotClient | None = None) -> bool:
    tdir = Path(task_dir)
    prefix = f"custom_bot_{event}"
    fingerprint = hashlib.sha256(json.dumps([title, markdown, button_text, button_url], ensure_ascii=False).encode("utf-8")).hexdigest()
    with get_task_lock(tdir):
        meta = load_task_meta(tdir)
        if notification_channel(meta) != "feishu_custom_bot":
            return False
        if meta.get(f"{prefix}_status") in {"sending", "sent"} and meta.get(f"{prefix}_fingerprint") == fingerprint:
            return meta.get(f"{prefix}_status") == "sent"
        max_attempts = max(1, int(os.getenv("FEISHU_CUSTOM_BOT_MAX_ATTEMPTS", "3")))
        if meta.get(f"{prefix}_status") == "failed" and int(meta.get(f"{prefix}_attempt_count") or 0) >= max_attempts:
            return False
        update_task_meta(
            tdir,
            **{
                f"{prefix}_status": "sending",
                f"{prefix}_fingerprint": fingerprint,
                f"{prefix}_attempt_count": int(meta.get(f"{prefix}_attempt_count") or 0) + 1,
                f"{prefix}_last_attempt_at": _utc_now(),
                f"{prefix}_last_error": "",
            },
        )
    try:
        (client or FeishuCustomBotClient()).send_card(title, markdown, button_text=button_text, button_url=button_url)
    except Exception as exc:  # noqa: BLE001
        update_task_meta(tdir, **{f"{prefix}_status": "failed", f"{prefix}_last_error": str(exc)})
        return False
    update_task_meta(tdir, **{f"{prefix}_status": "sent", f"{prefix}_notified_at": _utc_now(), f"{prefix}_last_error": ""})
    return True


def notify_task_started(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None) -> bool:
    meta = load_task_meta(Path(task_dir))
    if notification_channel(meta) != "feishu_custom_bot":
        return False
    trigger = "邮件自动触发" if meta.get("trigger_source") == "email_auto" else "管理员手动启动"
    subjects = list((meta.get("trigger_metadata") or {}).get("email_subjects") or [])
    subject_text = "\n邮件主题：" + "；".join(str(item)[:120] for item in subjects[:5]) if subjects else ""
    text = f"任务编号：{meta.get('task_id', Path(task_dir).name)}\n触发方式：{trigger}\n触发时间：{meta.get('triggered_at', '')}\n当前阶段：{meta.get('current_stage', '')}{subject_text}"
    return _custom_once(Path(task_dir), "started", "信号矩阵全量对比任务已启动", text, client=custom_client)


def notify_task_failed(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None) -> bool:
    meta = load_task_meta(Path(task_dir))
    if notification_channel(meta) == "enterprise_app":
        return False
    if meta.get("status") not in {"failed", "requires_manual_check"}:
        return False
    text = f"任务编号：{meta.get('task_id', Path(task_dir).name)}\n失败阶段：{meta.get('current_stage', '')}\n原因：{str(meta.get('error') or '')[:800]}\n问题模块数量：{int(meta.get('full_compare_unrecognized_count') or 0)}"
    return _custom_once(Path(task_dir), "failed", "信号矩阵全量对比任务失败", text, button_text="进入管理员页面", button_url=_admin_url(), client=custom_client)


def notify_review_ready(task_dir: Path, *, enterprise_client: Any | None = None, custom_client: FeishuCustomBotClient | None = None) -> bool:
    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    channel = notification_channel(meta)
    if channel == "enterprise_app":
        if enterprise_client is None:
            return False
        from .result_notifier import notify_review_ready as notify_enterprise_review

        return notify_enterprise_review(enterprise_client, tdir, meta)
    if channel != "feishu_custom_bot" or meta.get("status") != "awaiting_review" or not meta.get("review_url"):
        return False
    text = (
        f"任务编号：{meta.get('task_id', tdir.name)}\n4.0输入Excel：{int(meta.get('input_40_count') or 0)}个\n"
        f"5.1输入Excel：{int(meta.get('input_51_count') or 0)}个\n历史版本跳过：{int(meta.get('full_compare_skipped_history_count') or 0)}个\n"
        f"待审核差异项：{int(meta.get('signal_total') or 0)}个"
    )
    return _custom_once(tdir, "review_ready", "信号矩阵全量对比审核已就绪", text, button_text="进入人工审核", button_url=str(meta["review_url"]), client=custom_client)


def notify_result_ready(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None) -> bool:
    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    if notification_channel(meta) != "feishu_custom_bot" or meta.get("status") not in {"final_exported", "delivered"}:
        return False
    meta = ensure_result_access(tdir)
    files = allowed_result_files(tdir)
    text = f"任务编号：{meta.get('task_id', tdir.name)}\n审核完成时间：{meta.get('review_completed_at', '')}\n最终文件状态：已生成\n结果文件数量：{len(files)}"
    return _custom_once(tdir, "result_ready", "信号矩阵全量对比最终结果已生成", text, button_text="进入结果下载页", button_url=str(meta.get("result_url") or ""), client=custom_client)


def scan_custom_notifications(custom_client: FeishuCustomBotClient | None = None) -> None:
    from .bot_task_store import scan_task_metas

    for tdir, meta in scan_task_metas():
        if notification_channel(meta) != "feishu_custom_bot":
            continue
        if meta.get("status") in {"failed", "requires_manual_check"}:
            notify_task_failed(tdir, custom_client=custom_client)
        elif meta.get("status") == "awaiting_review":
            notify_review_ready(tdir, custom_client=custom_client)
        elif meta.get("status") in {"final_exported", "delivered"}:
            notify_result_ready(tdir, custom_client=custom_client)
