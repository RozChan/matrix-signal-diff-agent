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
from .feishu_openapi_client import FeishuOpenAPIClient, FeishuOpenAPIError
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


def _result_retry_limit() -> int:
    return int(os.getenv("FEISHU_RESULT_DELIVERY_RETRY_LIMIT", "3"))


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
    if meta.get("notify_type") == "feishu_custom_bot":
        return False
    return meta.get("source") in {"feishu", "feishu_confluence", "auto_full_compare"}


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


def _openapi_file_target(meta: dict[str, Any]) -> dict[str, str]:
    target = _notification_target(meta)
    if "chat_id" in target:
        return {"chat_id": target["chat_id"]}
    return {"open_id": target["user_id"]}


def _send_result_file(client: Any, file_path: Path, meta: dict[str, Any]) -> dict[str, str]:
    mode = os.getenv("FEISHU_FILE_SEND_MODE", "openapi").strip().lower()
    if mode == "lark_cli":
        target = _notification_target(meta)
        message_id = client.send_file(file_path=file_path, timeout=int(os.getenv("FEISHU_FILE_SEND_TIMEOUT_SECONDS", "60")), **target)
        if not message_id:
            raise FeishuOpenAPIError("send_message", "lark-cli文件发送失败")
        return {"file_name": file_path.name, "file_key": "", "message_id": message_id}
    api = FeishuOpenAPIClient()
    return api.send_file(file_path, **_openapi_file_target(meta))


def _result_failure_fingerprint(task_dir: Path, meta: dict[str, Any], error: str) -> str:
    import hashlib

    raw = "\n".join(
        [
            str(meta.get("task_id", task_dir.name)),
            str(task_dir / "output" / FINAL_REVIEW_FILENAME),
            str(meta.get("result_delivery_attempt_count") or 0),
            str(error or "")[:300],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_attempt_result_delivery(meta: dict[str, Any], final_path: Path, now: datetime | None = None, *, force: bool = False) -> bool:
    if force:
        return True
    now = now or datetime.now(timezone.utc)
    if meta.get("status") in {"interrupted", "requires_manual_check", "delivered"}:
        return False
    if meta.get("status") != "final_exported":
        return False
    if not final_path.exists() or final_path.stat().st_size <= 0:
        return False
    status = meta.get("result_delivery_status") or "pending"
    if status in {"sent", "delivered", "sending"}:
        return False
    attempt_count = int(meta.get("result_delivery_attempt_count") or 0)
    if attempt_count >= _result_retry_limit() or meta.get("result_delivery_auto_retry_exhausted"):
        return False
    if status == "pending":
        return True
    if status == "failed":
        next_retry_at = _parse_iso(meta.get("result_delivery_next_retry_at"))
        return bool(next_retry_at and now >= next_retry_at)
    return False


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


def _send_result_failure_notice(client: Any, task_dir: Path, meta: dict[str, Any], error: str) -> None:
    fingerprint = _result_failure_fingerprint(task_dir, meta, error)
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if current.get("result_delivery_failure_notice_fingerprint") == fingerprint and current.get("result_delivery_failure_notice_status") in {"sending", "sent"}:
            return
        current = update_task_meta(
            task_dir,
            result_delivery_failure_notice_status="sending",
            result_delivery_failure_notice_fingerprint=fingerprint,
        )
    try:
        _send_text(
            client,
            f"最终结果文件发送失败。\n\n任务编号：{current.get('task_id', task_dir.name)}\n"
            f"失败阶段：{current.get('result_delivery_failed_stage') or 'send_message'}\n"
            f"失败文件：{current.get('result_delivery_failed_file') or ''}\n"
            f"原因：{error}\n\n结果文件仍保存在服务器，可使用补发功能。",
            current,
        )
    except Exception as exc:  # noqa: BLE001
        update_task_meta(task_dir, result_delivery_failure_notice_status="failed", result_delivery_failure_notice_error=str(exc))
        return
    update_task_meta(task_dir, result_delivery_failure_notice_status="sent", result_delivery_failure_notice_sent_at=_utc_now_iso(), result_delivery_failure_notice_error="")


def _mark_result_delivery_failed(task_dir: Path, error: str) -> dict[str, Any]:
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        attempt_count = int(current.get("result_delivery_attempt_count") or 0)
        exhausted = attempt_count >= _result_retry_limit()
        next_retry_at = ""
        if not exhausted:
            next_retry_at = (datetime.now(timezone.utc) + timedelta(minutes=_retry_delay_minutes(attempt_count))).isoformat(timespec="seconds")
        return update_task_meta(
            task_dir,
            result_delivery_status="failed",
            delivery_error=error,
            result_delivery_last_attempt_at=_utc_now_iso(),
            result_delivery_next_retry_at=next_retry_at,
            result_delivery_auto_retry_exhausted=exhausted,
        )


def deliver_results(client: Any, task_dir: Path, meta: dict[str, Any], *, force: bool = False) -> bool:
    final_path = task_dir / "output" / FINAL_REVIEW_FILENAME
    with task_lock(task_dir):
        current = load_task_meta(task_dir)
        if meta:
            current.update({key: value for key, value in meta.items() if key not in current})
        if current.get("status") == "final_exported" and (not final_path.exists() or final_path.stat().st_size <= 0):
            update_task_meta(task_dir, result_delivery_status="failed", delivery_error="最终审核结果文件不存在或为空")
            return False
        if not should_attempt_result_delivery(current, final_path, force=force):
            status = current.get("result_delivery_status") or "pending"
            if status == "failed" and not current.get("result_delivery_next_retry_at") and not force:
                update_task_meta(task_dir, result_delivery_auto_retry_exhausted=True)
            return current.get("result_delivery_status") in {"sent", "delivered"} or current.get("status") == "delivered"
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
            result_delivery_next_retry_at="",
            result_delivery_auto_retry_exhausted=False,
            delivery_error="",
        )
    try:
        zip_path = build_results_zip(task_dir)
        _send_text(client, "最终结果已生成，正在上传并发送文件。", current)
        delivered_files = []
        for path in [final_path, zip_path]:
            try:
                delivered_files.append(_send_result_file(client, path, current))
            except FeishuOpenAPIError as exc:
                update_task_meta(task_dir, result_delivery_failed_stage=exc.stage, result_delivery_failed_file=path.name)
                raise
        if len(delivered_files) == 2:
            atomic_write_json(bot_dir(task_dir) / "delivery_state.json", {"delivered_files": delivered_files, "status": "sent"})
            update_task_meta(task_dir, result_delivery_status="delivered", status="delivered", result_delivered_at=_utc_now_iso(), delivery_error="", delivered_files=delivered_files)
            _send_text(client, f"最终结果文件已发送。\n\n任务编号：{current.get('task_id', task_dir.name)}\n文件名：{FINAL_REVIEW_FILENAME}", current)
            return True
        failed_meta = _mark_result_delivery_failed(task_dir, "飞书文件发送失败")
        _send_result_failure_notice(client, task_dir, failed_meta, "飞书文件发送失败")
        if failed_meta.get("result_delivery_auto_retry_exhausted"):
            _send_text(client, f"最终结果自动发送已停止。\n任务编号：{current.get('task_id', task_dir.name)}\n请使用人工补发命令。", failed_meta)
        return False
    except Exception as exc:  # noqa: BLE001
        failed_meta = _mark_result_delivery_failed(task_dir, str(exc))
        _send_result_failure_notice(client, task_dir, failed_meta, str(exc))
        if failed_meta.get("result_delivery_auto_retry_exhausted"):
            try:
                _send_text(client, f"最终结果自动发送已停止。\n任务编号：{current.get('task_id', task_dir.name)}\n请使用人工补发命令。", failed_meta)
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
