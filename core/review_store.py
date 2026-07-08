"""Persistent storage helpers for the local human review workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AI_REVIEW_SHEET = "AI辅助复核与人工审核明细"
REVIEW_ITEMS_FILE = "review_items.json"
REVIEW_STATE_FILE = "review_state.json"
REVIEW_LOG_FILE = "review_log.jsonl"
TASK_META_FILE = "task_meta.json"

MANUAL_REVIEW_RESULTS = ["确认真实差异", "确认可忽略", "确认错别字", "确认语义一致", "存疑待确认"]

ITEM_HEADER_MAP = {
    "来源Sheet": "source_sheet",
    "4.0信号名": "signal_40",
    "5.1信号名": "signal_51",
    "差异字段": "diff_field",
    "4.0内容": "value_40",
    "5.1内容": "value_51",
    "原始差异点list": "original_diff_list",
    "AI是否复核": "ai_reviewed",
    "AI判断结果": "ai_judgement",
    "差异类型": "difference_type",
    "置信度": "confidence",
    "AI判断理由": "ai_reason",
    "AI建议处理方式": "ai_suggested_action",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _task_meta_path(task_dir: Path) -> Path:
    return Path(task_dir) / TASK_META_FILE


def _review_items_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_ITEMS_FILE


def _review_state_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_STATE_FILE


def _review_log_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_LOG_FILE


def create_task_meta(
    task_dir: Path,
    task_id: str,
    input_40_count: int = 0,
    input_51_count: int = 0,
    status: str = "created",
) -> dict[str, Any]:
    task_path = Path(task_dir)
    output_dir = task_path / "output"
    review_dir = task_path / "review"
    now = utc_now_iso()
    meta = {
        "task_id": task_id,
        "created_at": now,
        "updated_at": now,
        "status": status,
        "input_40_count": input_40_count,
        "input_51_count": input_51_count,
        "output_dir": str(output_dir),
        "review_items_path": str(review_dir / REVIEW_ITEMS_FILE),
        "review_state_path": str(review_dir / REVIEW_STATE_FILE),
        "final_review_file": str(output_dir / "人工审核后最终差异结果.xlsx"),
        "error": "",
    }
    save_task_meta(task_path, meta)
    return meta


def load_task_meta(task_dir: Path) -> dict[str, Any]:
    return _read_json(_task_meta_path(Path(task_dir)), {})


def save_task_meta(task_dir: Path, meta: dict[str, Any]) -> None:
    meta = dict(meta)
    meta["updated_at"] = utc_now_iso()
    _atomic_write_json(_task_meta_path(Path(task_dir)), meta)


def update_task_meta(task_dir: Path, **updates: Any) -> dict[str, Any]:
    meta = load_task_meta(task_dir)
    meta.update(updates)
    save_task_meta(task_dir, meta)
    return meta


def build_item_id(item: dict[str, Any]) -> str:
    parts = [
        item.get("source_sheet", ""),
        item.get("signal_40", ""),
        item.get("signal_51", ""),
        item.get("diff_field", ""),
        item.get("value_40", ""),
        item.get("value_51", ""),
    ]
    raw = "\u241f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _header_map(ws) -> dict[str, int]:
    return {str(cell.value).strip(): idx for idx, cell in enumerate(ws[1], start=1) if cell.value is not None}


def _cell(ws, row_idx: int, headers: dict[str, int], title: str) -> str:
    col = headers.get(title)
    if not col:
        return ""
    value = ws.cell(row=row_idx, column=col).value
    return "" if value is None else str(value).strip()


def generate_review_items_from_excel(compare_file_path: Path, review_dir: Path) -> list[dict[str, str]]:
    from openpyxl import load_workbook

    review_path = Path(review_dir)
    review_path.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(compare_file_path, read_only=True, data_only=True)
    try:
        if AI_REVIEW_SHEET not in wb.sheetnames:
            raise ValueError(f"最终差异文件缺少 sheet：{AI_REVIEW_SHEET}")
        ws = wb[AI_REVIEW_SHEET]
        headers = _header_map(ws)
        items: list[dict[str, str]] = []
        seen: dict[str, int] = {}
        for row_idx in range(2, (ws.max_row or 1) + 1):
            item = {field: _cell(ws, row_idx, headers, title) for title, field in ITEM_HEADER_MAP.items()}
            if not any(item.values()):
                continue
            base_id = build_item_id(item)
            count = seen.get(base_id, 0)
            seen[base_id] = count + 1
            item["item_id"] = base_id if count == 0 else f"{base_id}_{count + 1}"
            items.append(item)
    finally:
        wb.close()

    _atomic_write_json(_review_items_path(review_path), items)
    return items


def load_review_items(review_dir: Path) -> list[dict[str, Any]]:
    return _read_json(_review_items_path(Path(review_dir)), [])


def init_review_state(review_dir: Path, task_id: str) -> dict[str, Any]:
    state = {"task_id": task_id, "updated_at": utc_now_iso(), "items": {}}
    save_review_state(review_dir, state)
    return state


def load_review_state(review_dir: Path) -> dict[str, Any]:
    return _read_json(_review_state_path(Path(review_dir)), {"task_id": "", "updated_at": "", "items": {}})


def save_review_state(review_dir: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["updated_at"] = utc_now_iso()
    state.setdefault("items", {})
    _atomic_write_json(_review_state_path(Path(review_dir)), state)


def append_review_log(review_dir: Path, entry: dict[str, Any]) -> None:
    log_entry = {"time": utc_now_iso(), **entry}
    log_path = _review_log_path(Path(review_dir))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def update_review_item(
    review_dir: Path,
    task_id: str,
    item_id: str,
    manual_review_result: str,
    manual_note: str,
    reviewer: str = "",
) -> dict[str, Any]:
    if manual_review_result and manual_review_result not in MANUAL_REVIEW_RESULTS:
        raise ValueError(f"不支持的人工审核结果：{manual_review_result}")
    state = load_review_state(review_dir)
    if not state.get("task_id"):
        state["task_id"] = task_id
    reviewed = bool(manual_review_result)
    state.setdefault("items", {})[item_id] = {
        "manual_review_result": manual_review_result,
        "manual_note": manual_note,
        "reviewed": reviewed,
        "reviewed_at": utc_now_iso() if reviewed else "",
        "reviewer": reviewer,
    }
    save_review_state(review_dir, state)
    try:
        append_review_log(
            review_dir,
            {
                "task_id": task_id,
                "item_id": item_id,
                "action": "update_review",
                "manual_review_result": manual_review_result,
                "manual_note": manual_note,
            },
        )
    except OSError:
        pass
    return state


def compute_review_stats(items: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, int | str]:
    state_items = state.get("items", {}) if isinstance(state, dict) else {}
    stats: dict[str, int | str] = {
        "total": len(items),
        "reviewed": 0,
        "unreviewed": 0,
        "confirmed_real_diff": 0,
        "ignored": 0,
        "typo": 0,
        "semantic_same": 0,
        "uncertain": 0,
        "updated_at": state.get("updated_at", "") if isinstance(state, dict) else "",
    }
    for item in items:
        review = state_items.get(item.get("item_id"), {})
        result = review.get("manual_review_result", "")
        if result:
            stats["reviewed"] = int(stats["reviewed"]) + 1
        else:
            stats["unreviewed"] = int(stats["unreviewed"]) + 1
        if result == "确认真实差异":
            stats["confirmed_real_diff"] = int(stats["confirmed_real_diff"]) + 1
        elif result == "确认可忽略":
            stats["ignored"] = int(stats["ignored"]) + 1
        elif result == "确认错别字":
            stats["typo"] = int(stats["typo"]) + 1
        elif result == "确认语义一致":
            stats["semantic_same"] = int(stats["semantic_same"]) + 1
        elif result == "存疑待确认":
            stats["uncertain"] = int(stats["uncertain"]) + 1
    return stats
