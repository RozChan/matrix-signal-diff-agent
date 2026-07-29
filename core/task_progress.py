"""Read-only task progress snapshots for administrator UI surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .confluence_task_store import load_confluence_sources
from .review_store import load_task_meta

BEIJING = ZoneInfo("Asia/Shanghai")
ACTIVE_STATUSES = {"created", "queued", "pending", "downloading", "ready", "running", "processing", "ai_reviewing", "ai_review_done", "generating_review"}
TERMINAL_STATUSES = {"failed", "requires_manual_check", "cancelled", "final_exported", "delivered"}


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def beijing_time(value: Any) -> str:
    parsed = parse_time(value)
    return parsed.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S") if parsed else "-"


def elapsed_text(start: Any, end: Any = None) -> str:
    started = parse_time(start)
    if not started:
        return "-"
    finished = parse_time(end) or datetime.now(timezone.utc)
    seconds = max(0, int((finished - started).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f"{hours}小时" if hours else "") + (f"{minutes}分" if minutes or hours else "") + f"{seconds}秒"


def status_label(status: str) -> str:
    return {
        "created": "已创建", "queued": "排队中", "pending": "等待中", "scanning": "扫描中", "downloading": "下载中", "completed": "已完成",
        "ready": "准备执行", "running": "执行中", "processing": "处理中", "ai_reviewing": "AI复核中",
        "ai_review_done": "生成审核数据", "generating_review": "生成审核数据", "awaiting_review": "等待人工审核",
        "reviewing": "人工审核中", "final_exported": "结果已生成", "delivered": "已完成",
        "failed": "失败", "requires_manual_check": "需要人工处理", "cancelled": "已取消",
    }.get(status, status or "未知")


def trigger_label(source: str) -> str:
    return {"manual_admin": "管理员手动", "email_auto": "邮件自动", "feishu_command": "飞书命令"}.get(source, source or "未知")


def overall_percent(meta: dict[str, Any]) -> int:
    status = str(meta.get("status") or "")
    if status in {"delivered", "final_exported"}:
        return 100
    if status in {"awaiting_review", "reviewing"}:
        return 95
    raw = max(0, min(100, int(meta.get("stage_progress") or 0)))
    stage = str(meta.get("current_stage") or "")
    floors = [
        (("任务创建",), 1), (("解析", "扫描"), 5), (("识别模块",), 8), (("选择最新",), 10),
        (("下载4.0",), 15), (("下载5.1",), 25), (("开始信号矩阵", "文件接收"), 35),
        (("生成全量清单",), 40), (("AI辅助复核",), 70), (("生成网页审核数据",), 90), (("等待人工审核",), 95),
    ]
    floor = max((value for needles, value in floors if any(needle in stage for needle in needles)), default=0)
    # Existing worker percentages are authoritative. Terminal failures/cancellation
    # preserve the last real percentage instead of pretending completion.
    return max(raw, floor)


def _source_snapshot(source: dict[str, Any], version: str, input_count: int) -> dict[str, Any]:
    attachments = list(source.get("attachments") or [])
    errors = list(source.get("errors") or [])
    selected_pages = {str(item.get("page_id")) for item in attachments if item.get("page_id")}
    return {
        "version": version,
        "status": str(source.get("status") or "pending"),
        "selected_pages": int(source.get("selected_page_count") or source.get("module_selected_count") or len(selected_pages)),
        "total_files": int(source.get("attachment_count") or len(attachments) or input_count),
        "downloaded_files": int(source.get("downloaded_count") or len(attachments) or input_count),
        "failed_files": len(errors),
        "input_files": input_count,
    }


def build_task_progress(task_dir: Path) -> dict[str, Any]:
    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    sources = load_confluence_sources(tdir, tdir.name).get("sources", [])
    by_version = {str(item.get("version")): item for item in sources}
    status = str(meta.get("status") or "")
    updated = meta.get("updated_at") or meta.get("review_completed_at") or meta.get("triggered_at") or meta.get("created_at")
    ai_total = int(meta.get("ai_required_signal_count") or meta.get("signal_total") or 0)
    ai_done = int(meta.get("ai_completed_signal_count") or 0)
    snapshot = {
        "task_id": str(meta.get("task_id") or tdir.name),
        "status": status,
        "status_label": status_label(status),
        "trigger_source": str(meta.get("trigger_source") or ""),
        "trigger_label": trigger_label(str(meta.get("trigger_source") or "")),
        "created_at": meta.get("created_at") or meta.get("triggered_at") or "",
        "created_at_display": beijing_time(meta.get("created_at") or meta.get("triggered_at")),
        "updated_at": updated or "",
        "updated_at_display": beijing_time(updated),
        "elapsed": elapsed_text(meta.get("created_at") or meta.get("triggered_at"), meta.get("review_completed_at") if status in TERMINAL_STATUSES else None),
        "current_stage": str(meta.get("current_stage") or "等待状态更新"),
        "message": str(meta.get("progress_message") or meta.get("message") or meta.get("error") or ""),
        "overall_percent": overall_percent(meta),
        "active": status in ACTIVE_STATUSES,
        "error": str(meta.get("error") or ""),
        "review_url": str(meta.get("review_url") or ""),
        "result_url": str(meta.get("result_url") or ""),
        "cancelled_at": beijing_time(meta.get("cancelled_at")),
        "ai": {
            "total": ai_total,
            "completed": ai_done,
            "failed": int(meta.get("ai_failed_signal_count") or 0),
            "percent": round(ai_done * 100 / ai_total, 1) if ai_total else 0,
            "current_signal": str(meta.get("current_signal") or ""),
        },
    }
    snapshot["sources"] = {
        version: _source_snapshot(by_version.get(version, {}), version, int(meta.get(f"input_{version.replace('.', '')}_count") or meta.get("input_40_count" if version == "4.0" else "input_51_count") or 0))
        for version in ("4.0", "5.1")
    }
    snapshot["steps"] = build_steps(snapshot)
    snapshot["events"] = recent_events(meta, sources)
    return snapshot


def build_steps(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    labels = ["任务创建", "Confluence父页面扫描", "最新版本页面选择", "4.0附件下载", "5.1附件下载", "legacy信号提取", "legacy同名去重", "legacy差异识别", "AI辅助复核", "生成审核数据", "等待人工审核", "生成最终结果", "任务完成"]
    states = ["已完成", *(["未开始"] * 12)]
    stage = snapshot["current_stage"]
    source40, source51 = snapshot["sources"]["4.0"], snapshot["sources"]["5.1"]
    scanning = any(source["status"] in {"scanning", "downloading", "completed", "failed"} for source in (source40, source51))
    selected = any(source["total_files"] > 0 for source in (source40, source51))
    if scanning:
        states[1] = "已完成" if all(source["status"] not in {"pending", "scanning"} for source in (source40, source51)) else "进行中"
    if selected:
        states[2] = "已完成" if all(source["status"] in {"downloading", "completed", "failed"} for source in (source40, source51)) else "进行中"
    for index, source in ((3, source40), (4, source51)):
        states[index] = "已完成" if source["status"] == "completed" else ("进行中" if source["status"] == "downloading" else ("失败" if source["status"] == "failed" else "未开始"))
    if "生成全量清单" in stage:
        states[5:8] = ["进行中"] * 3
    if "AI辅助复核" in stage or snapshot["status"] in {"ai_review_done", "generating_review", "awaiting_review", "reviewing", "final_exported", "delivered"}:
        states[5:8] = ["已完成"] * 3
        states[8] = "进行中"
    if snapshot["status"] in {"ai_review_done", "generating_review", "awaiting_review", "reviewing", "final_exported", "delivered"}:
        states[8] = "已完成"
        states[9] = "进行中" if snapshot["status"] in {"ai_review_done", "generating_review"} else "已完成"
    if snapshot["status"] in {"awaiting_review", "reviewing", "final_exported", "delivered"}:
        states[10] = "进行中" if snapshot["status"] in {"awaiting_review", "reviewing"} else "已完成"
    if snapshot["status"] in {"final_exported", "delivered"}:
        states[11] = "已完成"
        states[12] = "已完成"
    if snapshot["status"] in {"failed", "requires_manual_check", "cancelled"}:
        current = next((idx for idx, state in enumerate(states) if state == "进行中"), max((idx for idx, state in enumerate(states) if state == "已完成"), default=0) + 1)
        states[min(current, len(states) - 1)] = "已取消" if snapshot["status"] == "cancelled" else "失败"
    steps = [{"label": label, "state": state} for label, state in zip(labels, states)]
    return steps


def recent_events(meta: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    events = [{"time": beijing_time(meta.get("created_at") or meta.get("triggered_at")), "message": "任务创建成功"}]
    for source in sources:
        if source.get("updated_at"):
            events.append({"time": beijing_time(source.get("updated_at")), "message": f"{source.get('version', '')}来源：{status_label(str(source.get('status') or ''))}"})
    if meta.get("updated_at"):
        events.append({"time": beijing_time(meta.get("updated_at")), "message": str(meta.get("current_stage") or status_label(str(meta.get("status") or "")))})
    return sorted(events, key=lambda item: item["time"], reverse=True)[:20]


def choose_default_task(rows: list[dict[str, Any]], preferred: str = "") -> str:
    ids = {str(row.get("task_id")) for row in rows}
    if preferred in ids:
        return preferred
    active = next((row for row in rows if str(row.get("status")) in ACTIVE_STATUSES), None)
    if active:
        return str(active["task_id"])
    unfinished = next((row for row in rows if str(row.get("status")) not in TERMINAL_STATUSES | {"delivered"}), None)
    return str((unfinished or (rows[0] if rows else {})).get("task_id") or "")


def allowed_admin_actions(status: str) -> set[str]:
    if status in ACTIVE_STATUSES:
        return {"cancel", "details"}
    if status in {"failed", "requires_manual_check"}:
        return {"retry_confluence", "recreate", "details"}
    if status in {"awaiting_review", "reviewing"}:
        return {"review", "details"}
    if status in {"final_exported", "delivered"}:
        return {"results", "details", "manifest"}
    if status == "cancelled":
        return {"recreate", "details"}
    return {"details"}
