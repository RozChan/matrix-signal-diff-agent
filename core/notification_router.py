"""Route lifecycle notifications without coupling task sources to transports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .feishu_custom_bot import FeishuCustomBotClient
from .result_access import allowed_result_files, ensure_result_access
from .review_store import load_task_meta, update_task_meta
from .task_lock import get_task_lock
from .task_progress import beijing_time


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


def _notification_claim_path(task_dir: Path, prefix: str) -> Path:
    claim_dir = Path(task_dir).resolve() / "bot" / "notification_claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    return claim_dir / f"{prefix}.lock"


def _acquire_notification_claim(task_dir: Path, prefix: str) -> Path | None:
    """Atomically claim one notification across worker and scanner processes."""

    claim_path = _notification_claim_path(task_dir, prefix)
    try:
        stale_seconds = max(30, int(os.getenv("FEISHU_NOTIFICATION_CLAIM_STALE_SECONDS", "300")))
    except ValueError:
        stale_seconds = 300
    for _attempt in range(2):
        try:
            fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age_seconds = max(0.0, time.time() - claim_path.stat().st_mtime)
            except FileNotFoundError:
                continue
            if age_seconds <= stale_seconds:
                return None
            try:
                claim_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return None
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "claimed_at": _utc_now()}, ensure_ascii=False))
        except Exception:
            claim_path.unlink(missing_ok=True)
            raise
        return claim_path
    return None


def _release_notification_claim(claim_path: Path) -> None:
    try:
        claim_path.unlink(missing_ok=True)
    except OSError:
        # A stale claim is recoverable on the next scan. Notification success
        # is already persisted before this cleanup is attempted.
        pass


def _custom_once(
    task_dir: Path,
    event: str,
    title: str,
    markdown: str,
    *,
    button_text: str = "",
    button_url: str = "",
    buttons: list[dict[str, str]] | None = None,
    client: FeishuCustomBotClient | None = None,
    force: bool = False,
    content_sensitive: bool = False,
) -> bool:
    tdir = Path(task_dir)
    prefix = f"custom_bot_{event}"
    fingerprint = hashlib.sha256(json.dumps([title, markdown, button_text, button_url, buttons or []], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    claim_path = _acquire_notification_claim(tdir, prefix)
    if claim_path is None:
        current = load_task_meta(tdir)
        return current.get(f"{prefix}_status") == "sent" and (
            not content_sensitive or current.get(f"{prefix}_fingerprint") == fingerprint
        )
    try:
        with get_task_lock(tdir):
            meta = load_task_meta(tdir)
            if notification_channel(meta) != "feishu_custom_bot":
                return False
            if not force and meta.get(f"{prefix}_status") == "sent":
                if not content_sensitive or meta.get(f"{prefix}_fingerprint") == fingerprint:
                    return True
            max_attempts = max(1, int(os.getenv("FEISHU_CUSTOM_BOT_MAX_ATTEMPTS", "3")))
            if not force and meta.get(f"{prefix}_status") == "failed" and int(meta.get(f"{prefix}_attempt_count") or 0) >= max_attempts:
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
            sender = client or FeishuCustomBotClient()
            if buttons:
                sender.send_card(title, markdown, buttons=buttons)
            else:
                sender.send_card(title, markdown, button_text=button_text, button_url=button_url)
        except Exception as exc:  # noqa: BLE001
            update_task_meta(tdir, **{f"{prefix}_status": "failed", f"{prefix}_last_error": str(exc)})
            return False
        update_task_meta(tdir, **{f"{prefix}_status": "sent", f"{prefix}_notified_at": _utc_now(), f"{prefix}_last_error": ""})
        return True
    finally:
        _release_notification_claim(claim_path)


def notify_task_started(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None) -> bool:
    meta = load_task_meta(Path(task_dir))
    if notification_channel(meta) != "feishu_custom_bot":
        return False
    trigger = "邮件自动触发" if meta.get("trigger_source") == "email_auto" else "管理员手动启动"
    subjects = list((meta.get("trigger_metadata") or {}).get("email_subjects") or [])
    subject_text = "\n邮件主题：" + "；".join(str(item)[:120] for item in subjects[:5]) if subjects else ""
    text = f"任务编号：{meta.get('task_id', Path(task_dir).name)}\n触发方式：{trigger}\n触发时间：{beijing_time(meta.get('triggered_at'))}\n当前阶段：{meta.get('current_stage', '')}{subject_text}"
    return _custom_once(Path(task_dir), "started", "信号矩阵全量对比任务已启动", text, client=custom_client)


def notify_task_failed(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None) -> bool:
    meta = load_task_meta(Path(task_dir))
    if notification_channel(meta) == "enterprise_app":
        return False
    if meta.get("status") not in {"failed", "requires_manual_check"}:
        return False
    text = f"任务编号：{meta.get('task_id', Path(task_dir).name)}\n失败阶段：{meta.get('current_stage', '')}\n原因：{str(meta.get('error') or '')[:800]}\n问题模块数量：{int(meta.get('full_compare_unrecognized_count') or 0)}"
    return _custom_once(Path(task_dir), "failed", "信号矩阵全量对比任务失败", text, button_text="进入管理员页面", button_url=_admin_url(), client=custom_client)


def _review_mentions() -> str:
    open_ids = [item.strip() for item in os.getenv("FEISHU_REVIEW_AT_OPEN_IDS", "").split(",") if item.strip()]
    valid_ids = [item for item in open_ids if re.fullmatch(r"ou_[A-Za-z0-9]+", item)]
    return " ".join(f"<at id={open_id}>审核人</at>" for open_id in valid_ids)


def notify_review_ready(task_dir: Path, *, enterprise_client: Any | None = None, custom_client: FeishuCustomBotClient | None = None, force: bool = False) -> bool:
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
    pending_count = int(meta.get("pending_manual_count") or 0)
    if pending_count <= 0:
        return False
    mention = _review_mentions()
    text = (
        (f"{mention}\n" if mention else "")
        + f"任务编号：{meta.get('task_id', tdir.name)}\n4.0输入Excel：{int(meta.get('input_40_count') or 0)}个\n"
        f"5.1输入Excel：{int(meta.get('input_51_count') or 0)}个\n"
        f"待人工确认：{pending_count}项\n历史人工复用：{int(meta.get('history_reused_count') or 0)}项\n"
        f"生成时间：{beijing_time(meta.get('updated_at') or meta.get('created_at'))}"
    )
    return _custom_once(tdir, "review_ready", "信号差异人工审核已就绪", text, button_text="打开人工审核页面", button_url=str(meta["review_url"]), client=custom_client, force=force)


def notify_result_ready(task_dir: Path, *, custom_client: FeishuCustomBotClient | None = None, force: bool = False) -> bool:
    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    if notification_channel(meta) != "feishu_custom_bot" or meta.get("status") not in {"final_exported", "delivered"}:
        return False
    sheet_delivery = dict(meta.get("feishu_sheet_delivery") or {})
    sheet_enabled = os.getenv("FEISHU_RESULT_SHEET_DELIVERY_ENABLED", "true").strip().lower() == "true"
    spreadsheets = dict(sheet_delivery.get("spreadsheets") or {})
    sheet_keys = ("full_40", "full_51", "compare_final")
    sheets_ready = (
        sheet_delivery.get("status") in {"ready", "delivered"}
        and all(
            (spreadsheets.get(key) or {}).get("status") == "success"
            and str((spreadsheets.get(key) or {}).get("url") or "").startswith("https://")
            and (spreadsheets.get(key) or {}).get("permission_status") == "tenant_editable"
            for key in sheet_keys
        )
    )
    if sheet_enabled and sheets_ready:
        link_keys = ("compare_final", "full_40", "full_51")
        text = (
            f"任务编号：{meta.get('task_id', tdir.name)}\n"
            f"完成时间：{beijing_time(meta.get('review_completed_at'))}\n"
            "最终结果状态：已生成\n"
            + "\n".join(str(spreadsheets[key]["url"]) for key in link_keys)
        )
        return _custom_once(
            tdir,
            "result_ready",
            "信号矩阵全量对比最终结果已生成",
            text,
            client=custom_client,
            force=force,
            content_sensitive=True,
        )
    if sheet_enabled and sheet_delivery.get("status") == "failed":
        meta = ensure_result_access(tdir)
        error = str(sheet_delivery.get("last_error") or meta.get("delivery_error") or "未知错误")[:800]
        text = (
            f"任务编号：{meta.get('task_id', tdir.name)}\n"
            f"完成时间：{beijing_time(meta.get('review_completed_at'))}\n"
            "最终文件状态：已生成\n"
            "飞书云表格交付状态：失败\n"
            f"失败原因：{error}\n"
            "本地结果仍已保留，可从结果下载页获取。"
        )
        return _custom_once(
            tdir,
            "result_ready",
            "信号矩阵全量对比最终结果已生成",
            text,
            button_text="进入结果下载页",
            button_url=str(meta.get("result_url") or ""),
            client=custom_client,
            force=force,
            content_sensitive=True,
        )
    if sheet_enabled:
        return False
    delivery = dict(meta.get("feishu_delivery") or {})
    doc_enabled = os.getenv("FEISHU_DOC_DELIVERY_ENABLED", "false").strip().lower() == "true"
    attachments = dict(delivery.get("attachments") or {})
    attachment_keys = ("full_40", "full_51", "compare_final")
    document_ready = (
        delivery.get("status") in {"ready", "delivered"}
        and bool(delivery.get("document_url"))
        and all((attachments.get(key) or {}).get("status") == "success" for key in attachment_keys)
    )
    delivery_failed = delivery.get("status") in {"failed", "partial_failed"}
    if doc_enabled and not document_ready and not delivery_failed:
        return False
    meta = ensure_result_access(tdir)
    files = allowed_result_files(tdir)
    if document_ready:
        text = (
            f"任务编号：{meta.get('task_id', tdir.name)}\n"
            f"完成时间：{beijing_time(meta.get('review_completed_at'))}\n"
            "最终文件状态：已生成\n"
            "结果附件数量：3\n"
            "飞书交付状态：成功\n"
            f"飞书文档标题：{delivery.get('document_title') or '信号矩阵全量对比最终结果'}"
        )
        return _custom_once(
            tdir,
            "result_ready",
            "信号矩阵全量对比最终结果已生成",
            text,
            button_text="打开飞书结果文档",
            button_url=str(delivery["document_url"]),
            client=custom_client,
            force=force,
            content_sensitive=True,
        )
    if doc_enabled and delivery_failed:
        error = str(delivery.get("last_error") or meta.get("delivery_error") or "未知错误")[:800]
        text = (
            f"任务编号：{meta.get('task_id', tdir.name)}\n"
            f"完成时间：{beijing_time(meta.get('review_completed_at'))}\n"
            "最终文件状态：已生成\n"
            "飞书交付状态：失败\n"
            f"失败原因：{error}\n"
            "本地结果仍已保留，可从结果下载页获取。"
        )
        return _custom_once(
            tdir,
            "result_ready",
            "信号矩阵全量对比最终结果已生成",
            text,
            button_text="进入结果下载页",
            button_url=str(meta.get("result_url") or ""),
            client=custom_client,
            force=force,
            content_sensitive=True,
        )
    text = (
        f"任务编号：{meta.get('task_id', tdir.name)}\n"
        f"完成时间：{beijing_time(meta.get('review_completed_at'))}\n"
        f"最终文件状态：已生成\n结果文件数量：{len(files)}"
    )
    return _custom_once(
        tdir,
        "result_ready",
        "信号矩阵全量对比最终结果已生成",
        text,
        button_text="进入结果下载页",
        button_url=str(meta.get("result_url") or ""),
        client=custom_client,
        force=force,
        content_sensitive=True,
    )


def scan_custom_notifications(custom_client: FeishuCustomBotClient | None = None) -> None:
    from .bot_task_store import scan_task_metas

    for tdir, meta in scan_task_metas():
        if notification_channel(meta) != "feishu_custom_bot":
            continue
        if meta.get("status") in {"failed", "requires_manual_check"}:
            notify_task_failed(tdir, custom_client=custom_client)
        elif meta.get("status") == "awaiting_review":
            if int(meta.get("pending_manual_count") or 0) <= 0:
                from .task_finalization import auto_finalize_if_no_pending

                auto_finalize_if_no_pending(tdir, notify=True)
            else:
                notify_review_ready(tdir, custom_client=custom_client)
        elif meta.get("status") in {"final_exported", "delivered"}:
            if meta.get("status") == "final_exported":
                from .feishu_sheet_delivery import deliver_task_result_sheets

                deliver_task_result_sheets(tdir, custom_client=custom_client)
