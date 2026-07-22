"""Pure helpers and revision-safe batch persistence for the compact review table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .review_store import MANUAL_REVIEW_RESULTS, load_review_state, update_review_item

TABLE_RESULTS = tuple(MANUAL_REVIEW_RESULTS)
PENDING_REVIEW_LABEL = "🔴 待选择　　　| 未审核"


def review_result_display(value: Any) -> str:
    result = str(value or "")
    if result not in TABLE_RESULTS:
        return PENDING_REVIEW_LABEL
    padded = result + ("　" if len(result) < 6 else "")
    return f"🟢 {padded} | 已审核"


def review_result_value(value: Any) -> str:
    text = str(value or "")
    if text == PENDING_REVIEW_LABEL:
        return ""
    if text.startswith("🟢 ") and " | 已审核" in text:
        return text.removeprefix("🟢 ").removesuffix(" | 已审核").strip()
    return text


def diff_summary(item: dict[str, Any], limit: int | None = None) -> str:
    parts = []
    for diff in item.get("field_diffs") or []:
        field = str(diff.get("diff_field") or "未解析")
        value40 = str(diff.get("value_40") or "<空>").replace("\n", " ↵ ")
        value51 = str(diff.get("value_51") or "<空>").replace("\n", " ↵ ")
        parts.append(f"{field}：4.0={value40}；5.1={value51}")
    summary = "｜".join(parts) or "未解析到具体差异"
    return summary if limit is None or len(summary) <= limit else summary[: limit - 1] + "…"


def table_row(item: dict[str, Any], review: dict[str, Any], sequence: int, draft: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = draft or {}
    return {
        "row_id": str(item.get("item_id") or ""),
        "序号": sequence,
        "信号名": signal_name_display(item),
        "来源Sheet": str(item.get("source_sheet") or ""),
        "差异字段": "、".join(item.get("diff_fields") or []),
        "差异": diff_summary(item),
        "AI判断": str(item.get("signal_ai_judgement") or ""),
        "AI置信度": str(item.get("confidence") or ""),
        "审核结果": review_result_display(draft.get("manual_review_result", review.get("manual_review_result", ""))),
        "审核备注": draft.get("manual_note", review.get("manual_note", "")),
    }


def signal_name_display(item: dict[str, Any]) -> str:
    signal40 = str(item.get("signal_40") or "<空>")
    signal51 = str(item.get("signal_51") or "<空>")
    return signal40 if signal40 == signal51 else f"4.0: {signal40} ↔ 5.1: {signal51}"


def choose_exclusive_detail(checked_ids: list[str], previous: str) -> str:
    if not checked_ids:
        return ""
    if len(checked_ids) == 1:
        return checked_ids[0]
    return next((row_id for row_id in reversed(checked_ids) if row_id != previous), checked_ids[-1])


def apply_editor_changes(rows: list[dict[str, Any]], edited_rows: list[dict[str, Any]], drafts: dict[str, dict[str, Any]], state_items: dict[str, Any]) -> set[str]:
    dirty: set[str] = set()
    valid_ids = {str(row["row_id"]) for row in rows}
    for edited in edited_rows:
        row_id = str(edited.get("row_id") or "")
        if row_id not in valid_ids:
            continue
        review = state_items.get(row_id, {})
        result = review_result_value(edited.get("审核结果"))
        note = str(edited.get("审核备注") or "")
        drafts[row_id] = {"manual_review_result": result, "manual_note": note}
        if result != str(review.get("manual_review_result") or "") or note != str(review.get("manual_note") or ""):
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
        state = update_review_item(
            review_dir,
            task_id,
            row_id,
            str(draft.get("manual_review_result") or ""),
            str(draft.get("manual_note") or ""),
            base_revision=revision,
            session_id=session_id,
        )
        revision = int(state.get("revision") or revision + 1)
    return state


def pending_review_count(state: dict[str, Any]) -> int:
    return sum(1 for entry in state.get("items", {}).values() if not entry.get("reviewed") or not entry.get("manual_review_result"))
