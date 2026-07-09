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
NUMERIC_DIFF_FIELDS = {"信号长度", "精度", "偏移量", "物理最小值", "物理最大值"}
PRIORITY_AI_JUDGEMENTS = {"疑似一致", "疑似错别字", "疑似语义相近"}
UNCERTAIN_AI_JUDGEMENTS = {"无法判断", "未启用", ""}
SYSTEM_DEFAULT_SOURCE = "system_default"
MANUAL_SOURCE = "manual"

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


def is_priority_review_item(item: dict[str, Any]) -> bool:
    return item.get("ai_judgement", "") in PRIORITY_AI_JUDGEMENTS or item.get("ai_suggested_action", "") == "可忽略"


def is_system_default_keep_state(review: dict[str, Any]) -> bool:
    return review.get("review_source") == SYSTEM_DEFAULT_SOURCE and review.get("manual_review_result") == "确认真实差异"


def get_default_review_state(item: dict[str, Any]) -> dict[str, Any]:
    ai_judgement = str(item.get("ai_judgement") or "").strip()
    ai_action = str(item.get("ai_suggested_action") or "").strip()
    diff_field = str(item.get("diff_field") or "").strip()
    now = utc_now_iso()

    if ai_judgement == "真实差异" or ai_action == "应保留差异" or diff_field in NUMERIC_DIFF_FIELDS:
        return {
            "manual_review_result": "确认真实差异",
            "manual_note": "",
            "reviewed": True,
            "review_source": SYSTEM_DEFAULT_SOURCE,
            "default_review_result": "确认真实差异",
            "default_reason": "AI或规则判断为真实差异，系统默认保留；人工可修改",
            "reviewed_at": now,
            "updated_at": now,
            "reviewer": "",
        }

    if ai_judgement in PRIORITY_AI_JUDGEMENTS or ai_action == "可忽略":
        reason = "AI判断该差异疑似可忽略或可合并，需人工优先确认"
    else:
        reason = "AI无法给出可靠结论，需人工确认"

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


def _normalize_review_entry(entry: dict[str, Any], item: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = dict(entry) if isinstance(entry, dict) else {}
    default = get_default_review_state(item or {})
    result = str(normalized.get("manual_review_result") or "").strip()
    source = str(normalized.get("review_source") or "").strip()

    if not source:
        # Old states had no review_source. Preserve filled results as manual to avoid overwriting user work.
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
    state = {
        "task_id": existing.get("task_id") or task_id,
        "updated_at": existing.get("updated_at") or utc_now_iso(),
        "items": dict(existing.get("items", {})),
    }

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
            # Previous versions initialized empty per-item state. Apply safe system defaults
            # only when the record has not been manually edited and has no final result.
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
    previous = state.setdefault("items", {}).get(item_id, {})
    previous = _normalize_review_entry(previous)
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


def review_badge(item: dict[str, Any], review: dict[str, Any]) -> str:
    if review.get("review_source") == MANUAL_SOURCE:
        return "人工已修改"
    if is_system_default_keep_state(review):
        return "系统默认保留"
    if is_priority_review_item(item):
        return "需人工优先确认"
    return "待人工确认"


def review_sort_key(item: dict[str, Any], review: dict[str, Any]) -> tuple[int, str, str, str]:
    source = review.get("review_source", "")
    result = review.get("manual_review_result", "")
    ai = item.get("ai_judgement", "")
    if source != MANUAL_SOURCE and is_priority_review_item(item):
        priority = 0
    elif ai in UNCERTAIN_AI_JUDGEMENTS or not result:
        priority = 1
    elif source == MANUAL_SOURCE:
        priority = 2
    elif source == SYSTEM_DEFAULT_SOURCE and result == "确认真实差异":
        priority = 3
    else:
        priority = 4
    return (priority, item.get("source_sheet", ""), item.get("signal_40", ""), item.get("diff_field", ""))


def compute_review_stats(items: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, int | str]:
    state_items = state.get("items", {}) if isinstance(state, dict) else {}
    stats: dict[str, int | str] = {
        "total": len(items),
        "priority_review": 0,
        "system_default_keep": 0,
        "manual_modified": 0,
        "pending_manual": 0,
        "confirmed_real_diff": 0,
        "ignored": 0,
        "typo": 0,
        "semantic_same": 0,
        "uncertain": 0,
        "updated_at": state.get("updated_at", "") if isinstance(state, dict) else "",
    }
    for item in items:
        review = _normalize_review_entry(state_items.get(item.get("item_id"), {}), item)
        result = review.get("manual_review_result", "")
        source = review.get("review_source", "")
        if source != MANUAL_SOURCE and is_priority_review_item(item):
            stats["priority_review"] = int(stats["priority_review"]) + 1
        if is_system_default_keep_state(review):
            stats["system_default_keep"] = int(stats["system_default_keep"]) + 1
        if source == MANUAL_SOURCE:
            stats["manual_modified"] = int(stats["manual_modified"]) + 1
        if not review.get("reviewed"):
            stats["pending_manual"] = int(stats["pending_manual"]) + 1

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
