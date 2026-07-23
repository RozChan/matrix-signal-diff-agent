"""Field-level binary review helpers with revision-safe persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .review_store import is_manual_text_review_item, load_review_state, update_review_field

PENDING_REVIEW_LABEL = "🔴 待选择"
FIELD_RESULTS = ("same", "different")
REVIEWABLE_FIELDS = ("信号值描述", "单位")


def result_display(field_name: str, value: Any) -> str:
    result = str(value or "")
    if result not in FIELD_RESULTS:
        return PENDING_REVIEW_LABEL
    return f"🟢 {field_name}{'相同' if result == 'same' else '不同'}"


def result_value(value: Any) -> str:
    text = str(value or "")
    if text == PENDING_REVIEW_LABEL:
        return ""
    if text.endswith("相同"):
        return "same"
    if text.endswith("不同"):
        return "different"
    return text if text in FIELD_RESULTS else ""


def field_rows(items: list[dict[str, Any]], state_items: dict[str, Any], field_name: str, drafts: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    drafts = drafts or {}
    rows: list[dict[str, Any]] = []
    sequence = 0
    for item in items:
        if not is_manual_text_review_item(item):
            continue
        item_review = state_items.get(str(item.get("item_id") or ""), {})
        reviews = item_review.get("field_reviews", {})
        occurrences: dict[str, int] = {}
        for diff in item.get("field_diffs") or []:
            if str(diff.get("diff_field") or "") != field_name:
                continue
            occurrences[field_name] = occurrences.get(field_name, 0) + 1
            occurrence = occurrences[field_name]
            field_key = field_name if occurrence == 1 else f"{field_name}#{occurrence}"
            row_id = f"{item['item_id']}::{field_key}"
            review = reviews.get(field_key, {})
            draft = drafts.get(row_id, {})
            sequence += 1
            rows.append({
                "row_id": row_id, "item_id": item["item_id"], "field_key": field_key, "序号": sequence,
                "EEA4.0信号名": str(item.get("signal_40") or "<空>"),
                "EEA5.1信号名": str(item.get("signal_51") or "<空>"),
                f"EEA4.0{field_name}": str(diff.get("value_40") or "<空>"),
                f"EEA5.1{field_name}": str(diff.get("value_51") or "<空>"),
                "AI判断结果": str(item.get("signal_ai_judgement") or ""),
                "人工确认": result_display(field_name, draft.get("result", review.get("result", ""))),
            })
    return rows


def apply_editor_changes(rows: list[dict[str, Any]], edited_rows: list[dict[str, Any]], drafts: dict[str, dict[str, Any]], state_items: dict[str, Any]) -> set[str]:
    dirty: set[str] = set()
    by_id = {row["row_id"]: row for row in rows}
    for edited in edited_rows:
        row_id = str(edited.get("row_id") or "")
        row = by_id.get(row_id)
        if not row:
            continue
        result = result_value(edited.get("人工确认"))
        drafts[row_id] = {"item_id": row["item_id"], "field_key": row["field_key"], "result": result}
        saved = state_items.get(row["item_id"], {}).get("field_reviews", {}).get(row["field_key"], {}).get("result", "")
        if result != saved:
            dirty.add(row_id)
    return dirty


def save_dirty_reviews(review_dir: Path, task_id: str, drafts: dict[str, dict[str, Any]], dirty_ids: set[str], *, base_revision: int, session_id: str) -> dict[str, Any]:
    state = load_review_state(review_dir)
    if int(state.get("revision") or 0) != int(base_revision):
        from .review_store import ReviewConflictError
        raise ReviewConflictError("审核数据已被其他用户更新，请刷新页面")
    revision = int(base_revision)
    for row_id in sorted(dirty_ids):
        draft = drafts[row_id]
        state = update_review_field(
            review_dir, task_id, str(draft["item_id"]), str(draft["field_key"]), str(draft["result"]),
            reviewer=session_id, base_revision=revision, session_id=session_id,
        )
        revision = int(state.get("revision") or revision + 1)
    return state


def pending_review_count(state: dict[str, Any]) -> int:
    return sum(
        1 for entry in state.get("items", {}).values()
        for field in entry.get("field_reviews", {}).values()
        if not field.get("reviewed")
    )
