"""Result delivery helpers for Feishu tasks."""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir
from .bot_task_store import scan_task_metas
from .confluence_task_store import task_lock
from .final_export import FINAL_REVIEW_FILENAME
from .lark_cli_client import LarkCliError
from .pipeline import OUTPUT_FILENAMES
from .review_store import load_task_meta, update_task_meta


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _notification_retry_limit() -> int:
    return int(os.getenv("FEISHU_NOTIFICATION_RETRY_LIMIT", "3"))


def _retry_delay_minutes(attempt_number: int) -> int:
    return [1, 5, 15][min(max(attempt_number - 1, 0), 2)]


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_feishu_task(meta: dict[str, Any]) -> bool:
    return meta.get("source") in {"feishu", "feishu_confluence"}


def _notification_target(meta: dict[str, Any]) -> dict[str, str]:
    chat_id = str(meta.get("feishu_chat_id") or "").strip()
    user_id = str(meta.get("feishu_sender_id") or "").strip()
    if chat_id:
        if not chat_id.startswith("oc_"):
            raise LarkCliError("非法 feishu_chat_id：chat_id 必须以 oc_ 开头")
        return {"chat_id": chat_id}
    if user_id:
        if not user_id.startswith("ou_"):
            raise LarkCliError("非法 feishu_sender_id：user_id 必须以 ou_ 开头")
        return {"user_id": user_id}
    raise LarkCliError("缺少有效飞书接收目标：需要 oc_ chat_id 或 ou_ user_id")


def _send_text(client: Any, text: str, meta: dict[str, Any]) -> str | None:
    target = _notification_target(meta)
    return client.send_text(text=text, **target)


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
        retry_count = int(current.get("notification_retry_count") or 0)
        if not force and retry_count >= _notification_retry_limit():
            update_task_meta(task_dir, notification_status="failed", notification_error="通知重试次数已达上限")
            return False
        next_retry_at = _parse_iso(current.get("notification_next_retry_at"))
        if not force and next_retry_at and datetime.now(timezone.utc) < next_retry_at:
            return False
        review_url = current.get("review_url")
        if not review_url:
            update_task_meta(task_dir, notification_status="failed", notification_error="review_url缺失")
            return False
        try:
            _notification_target(current)
        except LarkCliError as exc:
            update_task_meta(
                task_dir,
                notification_status="failed",
                notification_error=str(exc),
                notification_retry_count=_notification_retry_limit(),
                notification_next_retry_at="",
            )
            return False
        current = update_task_meta(
            task_dir,
            notification_status="sending",
            notification_error="",
            notification_retry_count=(0 if force else retry_count) + 1,
            notification_last_attempt_at=_utc_now_iso(),
            notification_next_retry_at="",
        )
    text = build_review_ready_text(task_dir, current)
    try:
        msg_id = _send_text(client, text, current)
        if not msg_id:
            raise RuntimeError("飞书文本消息发送失败")
    except Exception as exc:  # noqa: BLE001
        retry_count = int(load_task_meta(task_dir).get("notification_retry_count") or 1)
        next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=_retry_delay_minutes(retry_count))
        with task_lock(task_dir):
            update_task_meta(
                task_dir,
                notification_status="failed",
                notification_error=str(exc),
                notification_last_attempt_at=_utc_now_iso(),
                notification_next_retry_at=next_retry_at.isoformat(timespec="seconds"),
            )
        return False
    with task_lock(task_dir):
        update_task_meta(task_dir, notification_status="sent", notification_sent_at=_utc_now_iso(), notification_error="", notification_next_retry_at="")
    return True


def notify_task_failed(client: Any, task_dir: Path, meta: dict[str, Any] | None = None) -> bool:
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if meta:
            current.update({key: value for key, value in meta.items() if key not in current})
        if current.get("failure_notification_status") == "sent":
            return True
        try:
            _notification_target(current)
        except LarkCliError as exc:
            update_task_meta(task_dir, failure_notification_status="failed", notification_error=str(exc))
            return False
        update_task_meta(task_dir, failure_notification_status="sending")
    text = f"信号矩阵差异识别任务失败\n\n任务编号：{current.get('task_id', task_dir.name)}\n错误：{current.get('error') or '未知错误'}"
    try:
        msg_id = _send_text(client, text, current)
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
    final_path = task_dir / "output" / FINAL_REVIEW_FILENAME
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if meta:
            current.update({key: value for key, value in meta.items() if key not in current})
        status = current.get("result_delivery_status") or "pending"
        if status in {"sent", "delivered"} or current.get("status") == "delivered":
            return True
        started_at = _parse_iso(current.get("result_delivery_started_at"))
        timeout_seconds = int(os.getenv("FEISHU_FILE_SEND_TIMEOUT_SECONDS", "120"))
        if status == "sending" and started_at and (datetime.now(timezone.utc) - started_at).total_seconds() < timeout_seconds:
            return False
        if not final_path.exists() or final_path.stat().st_size <= 0:
            update_task_meta(task_dir, result_delivery_status="failed", delivery_error="最终审核结果文件不存在或为空")
            return False
        try:
            _notification_target(current)
        except LarkCliError as exc:
            update_task_meta(task_dir, result_delivery_status="failed", delivery_error=str(exc))
            return False
        current = update_task_meta(
            task_dir,
            result_delivery_status="sending",
            result_delivery_started_at=_utc_now_iso(),
            result_delivery_attempt_count=int(current.get("result_delivery_attempt_count") or 0) + 1,
            delivery_error="",
        )
    try:
        zip_path = build_results_zip(task_dir)
        _send_text(client, "最终结果已生成，正在发送文件。", current)
        target = _notification_target(current)
        timeout_seconds = int(os.getenv("FEISHU_FILE_SEND_TIMEOUT_SECONDS", "120"))
        final_msg = client.send_file(file_path=final_path, timeout=timeout_seconds, **target)
        zip_msg = client.send_file(file_path=zip_path, timeout=timeout_seconds, **target)
        if final_msg and zip_msg:
            atomic_write_json(bot_dir(task_dir) / "delivery_state.json", {"final_message_id": final_msg, "zip_message_id": zip_msg, "status": "sent"})
            update_task_meta(task_dir, result_delivery_status="delivered", status="delivered", result_delivered_at=_utc_now_iso(), delivery_error="")
            _send_text(client, f"最终结果文件已发送。\n\n任务编号：{current.get('task_id', task_dir.name)}\n文件名：{FINAL_REVIEW_FILENAME}", current)
            return True
        update_task_meta(task_dir, result_delivery_status="failed", delivery_error="飞书文件发送失败")
        _send_text(client, f"最终结果文件发送失败。\n\n任务编号：{current.get('task_id', task_dir.name)}\n原因：飞书文件发送失败\n\n结果文件已保存在服务器，请联系维护人员或使用补发功能。", current)
        return False
    except Exception as exc:  # noqa: BLE001
        update_task_meta(task_dir, result_delivery_status="failed", delivery_error=str(exc))
        try:
            _send_text(client, f"最终结果文件发送失败。\n\n任务编号：{current.get('task_id', task_dir.name)}\n原因：{exc}\n\n结果文件已保存在服务器，请联系维护人员或使用补发功能。", current)
        except Exception:  # noqa: BLE001
            pass
        return False


def scan_and_notify(client: Any) -> None:
    for task_dir, meta in scan_task_metas():
        if not _is_feishu_task(meta):
            continue
        if meta.get("status") == "awaiting_review" and meta.get("notification_status") in {"pending", "failed", ""}:
            notify_review_ready(client, task_dir, meta)
        if meta.get("status") == "failed":
            notify_task_failed(client, task_dir, meta)
        if meta.get("status") == "final_exported" and meta.get("result_delivery_status") in {"pending", "failed", "sending", ""}:
            deliver_results(client, task_dir, meta)
