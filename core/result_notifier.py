"""Result delivery helpers for Feishu tasks."""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir
from .bot_task_store import scan_task_metas
from .confluence_task_store import task_lock
from .final_export import FINAL_REVIEW_FILENAME
from .pipeline import OUTPUT_FILENAMES
from .review_store import load_task_meta, update_task_meta


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _notification_retry_limit() -> int:
    return int(os.getenv("FEISHU_NOTIFICATION_RETRY_LIMIT", "3"))


def _is_feishu_task(meta: dict[str, Any]) -> bool:
    return meta.get("source") in {"feishu", "feishu_confluence"}


def _review_recipient(meta: dict[str, Any]) -> str:
    return str(meta.get("feishu_chat_id") or meta.get("feishu_sender_id") or "")


def build_review_ready_text(task_dir: Path, meta: dict[str, Any]) -> str:
    return (
        "信号矩阵差异识别已完成\n\n"
        f"任务编号：{meta.get('task_id', task_dir.name)}\n"
        f"4.0输入文件：{meta.get('input_40_count', 0)}个\n"
        f"5.1输入文件：{meta.get('input_51_count', 0)}个\n"
        f"待审核信号：{meta.get('signal_total', 0)}个\n\n"
        "请点击以下链接进入人工审核：\n"
        f"{meta.get('review_url', '')}"
    )


def build_results_zip(task_dir: Path) -> Path:
    output_dir = task_dir / "output"
    review_dir = task_dir / "review"
    zip_path = task_dir / f"全部结果_{task_dir.name}.zip"
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


def notify_review_ready(client: Any, task_dir: Path, meta: dict[str, Any] | None = None, *, force: bool = False) -> bool:
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if meta:
            current.update({key: value for key, value in meta.items() if key not in current})
        if current.get("status") != "awaiting_review":
            return False
        if current.get("notification_status") == "sent" and not force:
            return True
        if current.get("notification_status") == "sending" and not force:
            return False
        if not force and int(current.get("notification_retry_count") or 0) >= _notification_retry_limit():
            update_task_meta(task_dir, notification_status="failed", notification_error="通知重试次数已达上限")
            return False
        recipient = _review_recipient(current)
        review_url = current.get("review_url")
        if not review_url:
            update_task_meta(task_dir, notification_status="failed", notification_error="review_url缺失")
            return False
        if not recipient:
            update_task_meta(task_dir, notification_status="failed", notification_error="feishu_chat_id缺失")
            return False
        retry_count = 0 if force else int(current.get("notification_retry_count") or 0)
        current = update_task_meta(
            task_dir,
            notification_status="sending",
            notification_error="",
            notification_retry_count=retry_count + 1,
        )
    text = build_review_ready_text(task_dir, current)
    try:
        msg_id = client.send_text(recipient, text)
        if not msg_id:
            raise RuntimeError("飞书文本消息发送失败")
    except Exception as exc:  # noqa: BLE001
        with task_lock(task_dir):
            update_task_meta(task_dir, notification_status="failed", notification_error=str(exc))
        return False
    with task_lock(task_dir):
        update_task_meta(task_dir, notification_status="sent", notification_sent_at=_utc_now_iso(), notification_error="")
    return True


def notify_task_failed(client: Any, task_dir: Path, meta: dict[str, Any] | None = None) -> bool:
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if meta:
            current.update({key: value for key, value in meta.items() if key not in current})
        if current.get("failure_notification_status") == "sent":
            return True
        recipient = _review_recipient(current)
        if not recipient:
            update_task_meta(task_dir, failure_notification_status="failed", notification_error="feishu_chat_id缺失")
            return False
        update_task_meta(task_dir, failure_notification_status="sending")
    text = f"信号矩阵差异识别任务失败\n\n任务编号：{current.get('task_id', task_dir.name)}\n错误：{current.get('error') or '未知错误'}"
    try:
        msg_id = client.send_text(recipient, text)
        if not msg_id:
            raise RuntimeError("飞书失败通知发送失败")
    except Exception as exc:  # noqa: BLE001
        with task_lock(task_dir):
            update_task_meta(task_dir, failure_notification_status="failed", notification_error=str(exc))
        return False
    with task_lock(task_dir):
        update_task_meta(task_dir, failure_notification_status="sent", failure_notification_sent_at=_utc_now_iso())
    return True


def deliver_results(client: Any, task_dir: Path, meta: dict[str, Any]) -> bool:
    user_id = meta.get("feishu_sender_id")
    if not user_id:
        return False
    if meta.get("result_delivery_status") == "sent":
        return True
    final_path = task_dir / "output" / FINAL_REVIEW_FILENAME
    if not final_path.exists():
        update_task_meta(task_dir, result_delivery_status="failed", delivery_error="最终审核结果文件不存在")
        return False
    try:
        zip_path = build_results_zip(task_dir)
        client.send_text(user_id, f"任务 {meta.get('task_id', task_dir.name)} 已完成，正在发送结果文件。")
        final_msg = client.send_file(user_id, final_path)
        zip_msg = client.send_file(user_id, zip_path)
        if final_msg and zip_msg:
            atomic_write_json(bot_dir(task_dir) / "delivery_state.json", {"final_message_id": final_msg, "zip_message_id": zip_msg, "status": "sent"})
            update_task_meta(task_dir, result_delivery_status="sent", status="delivered", delivery_error="")
            return True
        update_task_meta(task_dir, result_delivery_status="failed", delivery_error="飞书文件发送失败")
        return False
    except Exception as exc:  # noqa: BLE001
        update_task_meta(task_dir, result_delivery_status="failed", delivery_error=str(exc))
        return False


def scan_and_notify(client: Any) -> None:
    for task_dir, meta in scan_task_metas():
        if not _is_feishu_task(meta):
            continue
        if meta.get("status") == "awaiting_review" and meta.get("notification_status") in {"pending", "failed", ""}:
            notify_review_ready(client, task_dir, meta)
        if meta.get("status") == "failed":
            notify_task_failed(client, task_dir, meta)
        if meta.get("status") == "final_exported" and meta.get("result_delivery_status") == "pending":
            deliver_results(client, task_dir, meta)
