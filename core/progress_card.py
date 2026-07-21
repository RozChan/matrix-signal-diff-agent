"""Unified Feishu task progress card rendering and update helpers."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .confluence_task_store import load_confluence_sources, task_lock
from .feishu_openapi_client import FeishuOpenAPIClient, FeishuOpenAPIError
from .review_store import load_task_meta, update_task_meta

TERMINAL_STATUSES = {"delivered"}
NO_CREATE_STATUSES = {"final_exported", "delivered"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _excel_count(task_dir: Path, version: str) -> int:
    return len(list((Path(task_dir) / "input" / version).glob("*.xls*")))


def build_task_progress_snapshot(task_dir: Path) -> dict[str, Any]:
    """Build progress from persisted task/confluence state and input dirs."""

    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    task_id = str(meta.get("task_id") or tdir.name)
    try:
        confluence = load_confluence_sources(tdir, task_id)
    except Exception:  # noqa: BLE001 - progress must not break business flow
        confluence = {"sources": []}
    sources = confluence.get("sources", []) if isinstance(confluence, dict) else []
    sources40 = [item for item in sources if item.get("version") == "4.0"]
    sources51 = [item for item in sources if item.get("version") == "5.1"]
    completed = [item for item in sources if item.get("status") == "completed"]
    failed = [item for item in sources if item.get("status") == "failed"]
    return {
        "task_id": task_id,
        "status": meta.get("status", ""),
        "current_stage": meta.get("current_stage", ""),
        "stage_progress": int(meta.get("stage_progress") or 0),
        "current_signal": meta.get("current_signal", ""),
        "input_40_count": int(meta.get("input_40_count") or _excel_count(tdir, "4.0")),
        "input_51_count": int(meta.get("input_51_count") or _excel_count(tdir, "5.1")),
        "excel_40_count": _excel_count(tdir, "4.0"),
        "excel_51_count": _excel_count(tdir, "5.1"),
        "confluence_source_count": len(sources),
        "confluence_completed_source_count": len(completed),
        "confluence_failed_source_count": len(failed),
        "confluence_40_source_count": len(sources40),
        "confluence_40_completed_source_count": sum(1 for item in sources40 if item.get("status") == "completed"),
        "confluence_51_source_count": len(sources51),
        "confluence_51_completed_source_count": sum(1 for item in sources51 if item.get("status") == "completed"),
        "ai_required_signal_count": int(meta.get("ai_required_signal_count") or meta.get("signal_total") or 0),
        "ai_completed_signal_count": int(meta.get("ai_completed_signal_count") or 0),
        "ai_failed_signal_count": int(meta.get("ai_failed_signal_count") or 0),
        "review_url": meta.get("review_url", ""),
        "error": meta.get("error", ""),
        "updated_at": meta.get("updated_at", ""),
        "result_delivery_status": meta.get("result_delivery_status", ""),
        "source": meta.get("source", ""),
        "trigger_source": meta.get("trigger_source", ""),
        "full_compare_40_parent_url": meta.get("full_compare_40_parent_url", ""),
        "full_compare_51_parent_url": meta.get("full_compare_51_parent_url", ""),
        "full_compare_module_count": int(meta.get("full_compare_module_count") or 0),
        "full_compare_selected_page_count": int(meta.get("full_compare_selected_page_count") or 0),
        "full_compare_skipped_history_count": int(meta.get("full_compare_skipped_history_count") or 0),
        "full_compare_unrecognized_count": int(meta.get("full_compare_unrecognized_count") or 0),
    }


def render_task_progress_text(snapshot: dict[str, Any]) -> str:
    lines = [
        "信号矩阵差异识别",
        "",
        f"任务编号：{snapshot.get('task_id', '')}",
        f"状态：{snapshot.get('status', '') or '处理中'}",
        f"总体进度：{snapshot.get('stage_progress', 0)}%",
        "",
        "输入文件：",
        f"4.0来源：{snapshot.get('confluence_40_completed_source_count', 0)} / {snapshot.get('confluence_40_source_count', 0)}",
        f"4.0 Excel：{snapshot.get('excel_40_count', snapshot.get('input_40_count', 0))}个",
        f"5.1来源：{snapshot.get('confluence_51_completed_source_count', 0)} / {snapshot.get('confluence_51_source_count', 0)}",
        f"5.1 Excel：{snapshot.get('excel_51_count', snapshot.get('input_51_count', 0))}个",
        f"失败来源：{snapshot.get('confluence_failed_source_count', 0)}个",
        "",
        f"当前阶段：{snapshot.get('current_stage', '')}",
        f"当前信号：{snapshot.get('current_signal', '') or '-'}",
        f"AI进度：{snapshot.get('ai_completed_signal_count', 0)} / {snapshot.get('ai_required_signal_count', 0)}",
        f"AI失败：{snapshot.get('ai_failed_signal_count', 0)}",
        f"最近更新时间：{_utc_now_iso()}",
    ]
    if snapshot.get("review_url"):
        lines.extend(["", "人工审核链接：", str(snapshot.get("review_url"))])
    if snapshot.get("error"):
        lines.extend(["", "错误信息：", str(snapshot.get("error"))[:1000]])
    if snapshot.get("confluence_failed_source_count"):
        lines.extend(["", "可回复：重试Confluence下载 / 忽略失败来源并开始处理"])
    if snapshot.get("source") == "auto_full_compare":
        lines[0] = "自动全量信号差异识别" if snapshot.get("status") != "awaiting_review" else "自动全量信号差异识别已完成"
        lines.extend([
            "",
            "触发方式：飞书命令模拟邮件触发" if snapshot.get("trigger_source") == "feishu_command" else f"触发方式：{snapshot.get('trigger_source', '')}",
            f"发现模块：{snapshot.get('full_compare_module_count', 0)}个",
            f"已选择最新版本：{snapshot.get('full_compare_selected_page_count', 0)}个",
            f"历史版本跳过：{snapshot.get('full_compare_skipped_history_count', 0)}个",
            f"无法识别：{snapshot.get('full_compare_unrecognized_count', 0)}个",
            f"4.0父页面：{snapshot.get('full_compare_40_parent_url', '')}",
            f"5.1父页面：{snapshot.get('full_compare_51_parent_url', '')}",
        ])
    return "\n".join(lines)


def render_task_progress_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    title = "信号矩阵差异识别"
    status = str(snapshot.get("status") or "处理中")
    if status == "awaiting_review":
        title = "信号矩阵差异识别已完成"
    elif status == "cancelled":
        title = "任务已取消"
    elif status == "failed":
        title = "任务处理失败"
    elif status == "delivered":
        title = "任务已完成"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": render_task_progress_text(snapshot)}]
    if snapshot.get("review_url"):
        elements.append({"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "进入人工审核"}, "type": "primary", "url": str(snapshot.get("review_url"))}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red" if status == "failed" else "blue", "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def progress_fingerprint(snapshot: dict[str, Any]) -> str:
    keys = [
        "status",
        "current_stage",
        "stage_progress",
        "current_signal",
        "confluence_completed_source_count",
        "confluence_failed_source_count",
        "excel_40_count",
        "excel_51_count",
        "ai_completed_signal_count",
        "ai_failed_signal_count",
        "ai_required_signal_count",
        "review_url",
        "error",
        "result_delivery_status",
        "full_compare_module_count",
        "full_compare_selected_page_count",
        "full_compare_skipped_history_count",
        "full_compare_unrecognized_count",
    ]
    raw = {key: snapshot.get(key) for key in keys}
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _should_update(meta: dict[str, Any], snapshot: dict[str, Any], fingerprint: str, *, force: bool = False) -> bool:
    if force:
        return True
    if meta.get("feishu_progress_last_fingerprint") == fingerprint:
        return False
    if not meta.get("feishu_progress_message_id"):
        return True
    if str(snapshot.get("status") or "") in {"failed", "awaiting_review", "final_exported", "delivered"}:
        return True
    if meta.get("feishu_progress_last_stage") != snapshot.get("current_stage"):
        return True
    last_percent = int(meta.get("feishu_progress_last_percent") or 0)
    delta = int(os.getenv("FEISHU_PROGRESS_UPDATE_MIN_PERCENT_DELTA", "3"))
    if int(snapshot.get("stage_progress") or 0) - last_percent >= delta:
        return True
    if int(meta.get("feishu_progress_last_ai_completed", 0) or 0) != int(snapshot.get("ai_completed_signal_count") or 0):
        return True
    if int(meta.get("feishu_progress_last_completed_sources", 0) or 0) != int(snapshot.get("confluence_completed_source_count") or 0):
        return True
    if int(meta.get("feishu_progress_last_failed_sources", 0) or 0) != int(snapshot.get("confluence_failed_source_count") or 0):
        return True
    last_at = _parse_time(meta.get("feishu_progress_last_updated_at"))
    min_interval = int(os.getenv("FEISHU_PROGRESS_UPDATE_MIN_INTERVAL_SECONDS", "5"))
    return bool(last_at and (datetime.now(timezone.utc) - last_at).total_seconds() >= min_interval)


def _target(meta: dict[str, Any]) -> dict[str, str]:
    chat_id = str(meta.get("feishu_chat_id") or "").strip()
    user_id = str(meta.get("feishu_sender_id") or "").strip()
    if chat_id:
        return {"chat_id": chat_id}
    if user_id:
        return {"open_id": user_id}
    raise FeishuOpenAPIError("send_message", "缺少飞书进度卡片接收目标")


def _client_send_card(client: Any | None, card: dict[str, Any], meta: dict[str, Any]) -> str:
    target = _target(meta)
    if client is not None and hasattr(client, "send_progress_card"):
        return str(client.send_progress_card(card, **target))
    openapi = FeishuOpenAPIClient()
    return openapi.send_progress_card(card, **target)


def _client_update_card(client: Any | None, message_id: str, card: dict[str, Any]) -> None:
    if client is not None and hasattr(client, "update_progress_card"):
        client.update_progress_card(message_id, card)
        return
    openapi = FeishuOpenAPIClient()
    openapi.update_progress_card(message_id, card)


def _looks_missing_message(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ["not found", "message_not_found", "不存在", "已撤回", "recalled", "deleted"])


def sync_task_progress_card(task_dir: Path, client: Any | None = None, *, force: bool = False) -> bool:
    """Create or update the one progress card for a task. Best-effort only."""

    tdir = Path(task_dir)
    with task_lock(tdir):
        meta = load_task_meta(tdir)
        if meta.get("status") in NO_CREATE_STATUSES and not meta.get("feishu_progress_message_id"):
            return False
        snapshot = build_task_progress_snapshot(tdir)
        fingerprint = progress_fingerprint(snapshot)
        if not _should_update(meta, snapshot, fingerprint, force=force):
            return False
        message_id = str(meta.get("feishu_progress_message_id") or "")
        update_task_meta(tdir, feishu_progress_update_error="")
    card = render_task_progress_card(snapshot)
    try:
        if message_id:
            _client_update_card(client, message_id, card)
            new_message_id = message_id
        else:
            new_message_id = _client_send_card(client, card, meta)
    except Exception as exc:  # noqa: BLE001
        if message_id and _looks_missing_message(exc):
            try:
                new_message_id = _client_send_card(client, card, meta)
            except Exception as send_exc:  # noqa: BLE001
                update_task_meta(tdir, feishu_progress_update_error=str(send_exc))
                return False
        else:
            update_task_meta(tdir, feishu_progress_update_error=str(exc))
            return False
    with task_lock(tdir):
        update_task_meta(
            tdir,
            feishu_progress_message_id=new_message_id,
            feishu_progress_last_updated_at=_utc_now_iso(),
            feishu_progress_last_stage=snapshot.get("current_stage", ""),
            feishu_progress_last_percent=int(snapshot.get("stage_progress") or 0),
            feishu_progress_last_fingerprint=fingerprint,
            feishu_progress_last_ai_completed=int(snapshot.get("ai_completed_signal_count") or 0),
            feishu_progress_last_completed_sources=int(snapshot.get("confluence_completed_source_count") or 0),
            feishu_progress_last_failed_sources=int(snapshot.get("confluence_failed_source_count") or 0),
            feishu_progress_update_error="",
        )
    return True
