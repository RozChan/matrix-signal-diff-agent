"""Result delivery helpers for Feishu tasks."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from .bot_task_store import atomic_write_json, bot_dir
from .bot_task_store import scan_task_metas
from .final_export import FINAL_REVIEW_FILENAME
from .pipeline import OUTPUT_FILENAMES
from .review_store import update_task_meta


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


def notify_review_ready(client: Any, task_dir: Path, meta: dict[str, Any]) -> bool:
    user_id = meta.get("feishu_sender_id")
    review_url = meta.get("review_url")
    if not user_id or not review_url:
        return False
    if meta.get("notification_status") == "sent":
        return True
    text = (
        f"AI辅助复核已完成\n任务编号：{meta.get('task_id', task_dir.name)}\n"
        f"信号级差异：{meta.get('signal_total', 0)}\n"
        f"需调用AI的文本类信号：{meta.get('ai_required_signal_count', 0)}\n"
        f"AI失败：{meta.get('ai_failed_signal_count', 0)}\n\n"
        f"请进入人工审核工作台：\n{review_url}"
    )
    msg_id = client.send_text(user_id, text)
    if msg_id:
        update_task_meta(task_dir, notification_status="sent")
        return True
    update_task_meta(task_dir, notification_status="failed")
    return False


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
        if meta.get("source") != "feishu":
            continue
        if meta.get("status") == "awaiting_review" and meta.get("notification_status") != "sent":
            notify_review_ready(client, task_dir, meta)
        if meta.get("status") == "final_exported" and meta.get("result_delivery_status") == "pending":
            deliver_results(client, task_dir, meta)
        if meta.get("status") == "running":
            update_task_meta(task_dir, status="interrupted", error="服务重启后无法确认后台 worker 仍在运行，请人工检查或重新发起任务。")
