"""Persistent storage helpers for the local signal-level human review workflow."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task_lock import get_task_lock

AI_REVIEW_SHEET = "AI辅助复核与人工审核明细"
REVIEW_ITEMS_FILE = "review_items.json"
REVIEW_STATE_FILE = "review_state.json"
REVIEW_LOG_FILE = "review_log.jsonl"
TASK_META_FILE = "task_meta.json"

MANUAL_REVIEW_RESULTS = ["确认真实差异", "确认可忽略", "确认错别字", "确认语义一致", "存疑待确认"]
SIGNAL_AI_JUDGEMENTS = ["真实差异", "疑似可忽略", "无法判断", "未启用"]
SYSTEM_DEFAULT_SOURCE = "system_default"
MANUAL_SOURCE = "manual"

ITEM_HEADER_MAP = {
    "来源Sheet": "source_sheet",
    "4.0信号名": "signal_40",
    "5.1信号名": "signal_51",
    "差异字段汇总": "diff_fields_text",
    "差异字段数量": "diff_field_count",
    "是否包含数值类差异": "has_numeric_diff",
    "是否包含文本类差异": "has_text_diff",
    "原始差异点list": "original_diff_list",
    "字段差异明细": "field_diff_details",
    "信号级AI判断结果": "signal_ai_judgement",
    "差异类型汇总": "difference_type_summary",
    "置信度": "confidence",
    "信号级AI判断理由": "signal_ai_reason",
    "信号级AI建议处理方式": "signal_ai_suggested_action",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    last_error: PermissionError | None = None
    for attempt in range(5):
        tmp_name = ""
        fd = -1
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
            return
        except PermissionError as exc:
            last_error = exc
            if fd >= 0:
                os.close(fd)
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            if attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))
        except Exception:
            if fd >= 0:
                os.close(fd)
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            raise
    if last_error:
        raise last_error


def _task_meta_path(task_dir: Path) -> Path:
    return Path(task_dir) / TASK_META_FILE


def _review_items_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_ITEMS_FILE


def _review_state_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_STATE_FILE


def _review_log_path(review_dir: Path) -> Path:
    return Path(review_dir) / REVIEW_LOG_FILE


def create_task_meta(task_dir: Path, task_id: str, input_40_count: int = 0, input_51_count: int = 0, status: str = "created") -> dict[str, Any]:
    with get_task_lock(Path(task_dir)):
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
    with get_task_lock(Path(task_dir)):
        meta = dict(meta)
        meta["updated_at"] = utc_now_iso()
        _atomic_write_json(_task_meta_path(Path(task_dir)), meta)


def update_task_meta(task_dir: Path, **updates: Any) -> dict[str, Any]:
    with get_task_lock(Path(task_dir)):
        meta = load_task_meta(task_dir)
        meta.update(updates)
        save_task_meta(task_dir, meta)
        return meta


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"是", "true", "True", "1"}


def _split_fields(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(",", "、").split("、") if part.strip()]


def _field_type_from_detail(line: str) -> str:
    if "数值类差异" in line:
        return "numeric"
    if "文本类差异" in line:
        return "text"
    return "unknown"


def _parse_field_diff_details(text: str, diff_fields: list[str]) -> list[dict[str, str]]:
    details = []
    if text:
        blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
        for block in blocks:
            lines = block.splitlines()
            field = lines[0].strip("【】") if lines else "未解析"
            value_40_lines: list[str] = []
            value_51_lines: list[str] = []
            field_type = "unknown"
            current_target = ""
            for line in lines[1:]:
                if line.startswith("4.0："):
                    current_target = "4.0"
                    value_40_lines.append(line.replace("4.0：", "", 1))
                elif line.startswith("5.1："):
                    current_target = "5.1"
                    value_51_lines.append(line.replace("5.1：", "", 1))
                elif line.startswith("类型："):
                    current_target = ""
                    field_type = _field_type_from_detail(line)
                elif current_target == "4.0":
                    value_40_lines.append(line)
                elif current_target == "5.1":
                    value_51_lines.append(line)
            details.append({"diff_field": field, "value_40": "\n".join(value_40_lines), "value_51": "\n".join(value_51_lines), "field_type": field_type})
    if details:
        return details
    return [{"diff_field": field, "value_40": "", "value_51": "", "field_type": "unknown"} for field in diff_fields]


def build_item_id(item: dict[str, Any]) -> str:
    parts = [item.get("source_sheet", ""), item.get("signal_40", ""), item.get("signal_51", ""), item.get("original_diff_list", "")]
    raw = "\u241f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def is_signal_level_item(item: dict[str, Any]) -> bool:
    return "field_diffs" in item and "signal_ai_judgement" in item


def _header_map(ws) -> dict[str, int]:
    return {str(cell.value).strip(): idx for idx, cell in enumerate(ws[1], start=1) if cell.value is not None}


def _cell(ws, row_idx: int, headers: dict[str, int], title: str) -> str:
    col = headers.get(title)
    if not col:
        return ""
    value = ws.cell(row=row_idx, column=col).value
    return "" if value is None else str(value).strip()


def generate_review_items_from_excel(compare_file_path: Path, review_dir: Path) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    review_path = Path(review_dir)
    review_path.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(compare_file_path, read_only=True, data_only=True)
    try:
        if AI_REVIEW_SHEET not in wb.sheetnames:
            raise ValueError(f"最终差异文件缺少 sheet：{AI_REVIEW_SHEET}")
        ws = wb[AI_REVIEW_SHEET]
        headers = _header_map(ws)
        items: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for row_idx in range(2, (ws.max_row or 1) + 1):
            raw = {field: _cell(ws, row_idx, headers, title) for title, field in ITEM_HEADER_MAP.items()}
            if not any(raw.values()):
                continue
            diff_fields = _split_fields(raw.get("diff_fields_text"))
            field_diffs = _parse_field_diff_details(raw.get("field_diff_details", ""), diff_fields)
            item: dict[str, Any] = {
                "source_sheet": raw.get("source_sheet", ""),
                "signal_40": raw.get("signal_40", ""),
                "signal_51": raw.get("signal_51", ""),
                "diff_fields": diff_fields,
                "diff_field_count": int(raw.get("diff_field_count") or len(field_diffs)),
                "has_numeric_diff": _as_bool(raw.get("has_numeric_diff")),
                "has_text_diff": _as_bool(raw.get("has_text_diff")),
                "original_diff_list": raw.get("original_diff_list", ""),
                "field_diffs": field_diffs,
                "field_diff_details": raw.get("field_diff_details", ""),
                "signal_ai_judgement": raw.get("signal_ai_judgement", ""),
                "difference_type_summary": raw.get("difference_type_summary", ""),
                "confidence": raw.get("confidence", ""),
                "signal_ai_reason": raw.get("signal_ai_reason", ""),
                "signal_ai_suggested_action": raw.get("signal_ai_suggested_action", ""),
            }
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


def get_default_review_state(item: dict[str, Any]) -> dict[str, Any]:
    judgement = str(item.get("signal_ai_judgement") or "").strip()
    now = utc_now_iso()
    if judgement == "真实差异":
        return {
            "manual_review_result": "确认真实差异",
            "manual_note": "",
            "reviewed": True,
            "review_source": SYSTEM_DEFAULT_SOURCE,
            "default_review_result": "确认真实差异",
            "default_reason": "AI或规则判断该信号存在真实差异，系统默认保留；人工可修改",
            "reviewed_at": now,
            "updated_at": now,
            "reviewer": "",
        }
    if judgement == "疑似可忽略":
        reason = "AI判断该信号差异疑似可忽略，需人工优先确认"
    else:
        reason = "AI未给出可直接采用的结论，需人工确认"
    return {
        "manual_review_result": "",
        "manual_note": "",
        "reviewed": False,
        "review_source": "",
        "default_review_result": "",
        "default_reason": reason,
        "reviewed_at": "",
        "updated_at": now,
        "reviewer": "",
    }


def _normalize_review_entry(entry: dict[str, Any], item: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = dict(entry) if isinstance(entry, dict) else {}
    default = get_default_review_state(item or {})
    result = str(normalized.get("manual_review_result") or "").strip()
    source = str(normalized.get("review_source") or "").strip()
    if not source:
        source = MANUAL_SOURCE if result else ""
    normalized["review_source"] = source
    normalized.setdefault("manual_review_result", result)
    normalized.setdefault("manual_note", "")
    normalized.setdefault("default_review_result", default.get("default_review_result", ""))
    normalized.setdefault("default_reason", default.get("default_reason", ""))
    normalized.setdefault("reviewed_at", "")
    normalized.setdefault("updated_at", normalized.get("reviewed_at") or utc_now_iso())
    normalized.setdefault("reviewer", "")
    if "reviewed" not in normalized:
        normalized["reviewed"] = bool(result)
    return normalized


def init_review_state(review_dir: Path, task_id: str, items: list[dict[str, Any]] | None = None, overwrite: bool = False) -> dict[str, Any]:
    review_path = Path(review_dir)
    item_list = items if items is not None else load_review_items(review_path)
    existing = load_review_state(review_path) if _review_state_path(review_path).exists() and not overwrite else {"items": {}}
    state = {"task_id": existing.get("task_id") or task_id, "updated_at": existing.get("updated_at") or utc_now_iso(), "items": dict(existing.get("items", {}))}
    changed = overwrite or not _review_state_path(review_path).exists()
    for item in item_list:
        item_id = item.get("item_id")
        if not item_id:
            continue
        current = state["items"].get(item_id)
        if current is None or overwrite:
            state["items"][item_id] = get_default_review_state(item)
            changed = True
        else:
            normalized = _normalize_review_entry(current, item)
            if normalized.get("review_source") != MANUAL_SOURCE and not normalized.get("manual_review_result"):
                default_state = get_default_review_state(item)
                if default_state.get("manual_review_result"):
                    normalized = default_state
            if normalized != current:
                state["items"][item_id] = normalized
                changed = True
    if changed:
        save_review_state(review_path, state)
    return state


def load_review_state(review_dir: Path) -> dict[str, Any]:
    state = _read_json(_review_state_path(Path(review_dir)), {"task_id": "", "updated_at": "", "items": {}})
    state.setdefault("task_id", "")
    state.setdefault("updated_at", "")
    state.setdefault("items", {})
    state["items"] = {item_id: _normalize_review_entry(entry) for item_id, entry in state.get("items", {}).items()}
    return state


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


def update_review_item(review_dir: Path, task_id: str, item_id: str, manual_review_result: str, manual_note: str, reviewer: str = "") -> dict[str, Any]:
    if manual_review_result and manual_review_result not in MANUAL_REVIEW_RESULTS:
        raise ValueError(f"不支持的人工审核结果：{manual_review_result}")
    state = load_review_state(review_dir)
    if not state.get("task_id"):
        state["task_id"] = task_id
    previous = _normalize_review_entry(state.setdefault("items", {}).get(item_id, {}))
    now = utc_now_iso()
    state["items"][item_id] = {
        "manual_review_result": manual_review_result,
        "manual_note": manual_note,
        "reviewed": True,
        "review_source": MANUAL_SOURCE,
        "default_review_result": previous.get("default_review_result", ""),
        "default_reason": previous.get("default_reason", ""),
        "reviewed_at": now,
        "updated_at": now,
        "reviewer": reviewer,
    }
    save_review_state(review_dir, state)
    try:
        append_review_log(review_dir, {"task_id": task_id, "item_id": item_id, "action": "update_review", "manual_review_result": manual_review_result, "manual_note": manual_note})
    except OSError:
        pass
    return state


def review_badge(item: dict[str, Any], review: dict[str, Any]) -> str:
    if review.get("review_source") == MANUAL_SOURCE:
        return "人工已修改"
    if review.get("review_source") == SYSTEM_DEFAULT_SOURCE and review.get("manual_review_result") == "确认真实差异":
        return "系统默认保留"
    if item.get("signal_ai_judgement") == "疑似可忽略":
        return "需人工优先确认"
    return "待人工确认"


def review_sort_key(item: dict[str, Any], review: dict[str, Any]) -> tuple[int, str, str, str]:
    source = review.get("review_source", "")
    result = review.get("manual_review_result", "")
    judgement = item.get("signal_ai_judgement", "")
    if judgement == "疑似可忽略" and source != MANUAL_SOURCE:
        priority = 0
    elif judgement in {"无法判断", "未启用"} and not result:
        priority = 1
    elif source == MANUAL_SOURCE:
        priority = 2
    elif judgement == "真实差异" and source == SYSTEM_DEFAULT_SOURCE:
        priority = 3
    else:
        priority = 4
    return (priority, item.get("source_sheet", ""), item.get("signal_40", ""), item.get("signal_51", ""))


def compute_review_stats(items: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, int | float | str]:
    state_items = state.get("items", {}) if isinstance(state, dict) else {}
    total_fields = sum(int(item.get("diff_field_count") or len(item.get("field_diffs", [])) or 0) for item in items)
    stats: dict[str, int | float | str] = {
        "total": len(items),
        "priority_review": 0,
        "pending_manual": 0,
        "system_default_keep": 0,
        "manual_modified": 0,
        "confirmed_real_diff": 0,
        "ignored": 0,
        "typo": 0,
        "semantic_same": 0,
        "uncertain": 0,
        "diff_field_total": total_fields,
        "avg_diff_fields_per_signal": round(total_fields / len(items), 2) if items else 0,
        "updated_at": state.get("updated_at", "") if isinstance(state, dict) else "",
    }
    for item in items:
        review = _normalize_review_entry(state_items.get(item.get("item_id"), {}), item)
        result = review.get("manual_review_result", "")
        source = review.get("review_source", "")
        if item.get("signal_ai_judgement") == "疑似可忽略" and source != MANUAL_SOURCE:
            stats["priority_review"] = int(stats["priority_review"]) + 1
        if not review.get("reviewed"):
            stats["pending_manual"] = int(stats["pending_manual"]) + 1
        if source == SYSTEM_DEFAULT_SOURCE and result == "确认真实差异":
            stats["system_default_keep"] = int(stats["system_default_keep"]) + 1
        if source == MANUAL_SOURCE:
            stats["manual_modified"] = int(stats["manual_modified"]) + 1
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
