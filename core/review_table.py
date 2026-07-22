"""Pure helpers and revision-safe batch persistence for the compact review table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .review_store import MANUAL_REVIEW_RESULTS, load_review_state, update_review_item

TABLE_RESULTS = tuple(MANUAL_REVIEW_RESULTS)


def diff_summary(item: dict[str, Any], limit: int = 100) -> str:
    fields = "、".join(item.get("diff_fields") or [])
    summary = f"{fields}（{int(item.get('diff_field_count') or 0)}项）"
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


def table_row(item: dict[str, Any], review: dict[str, Any], sequence: int, draft: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = draft or {}
    return {
        "row_id": str(item.get("item_id") or ""),
        "序号": sequence,
        "4.0信号名": str(item.get("signal_40") or ""),
        "5.1信号名": str(item.get("signal_51") or ""),
        "来源Sheet": str(item.get("source_sheet") or ""),
        "差异字段": "、".join(item.get("diff_fields") or []),
        "差异摘要": diff_summary(item),
        "AI建议": str(item.get("signal_ai_suggested_action") or item.get("signal_ai_judgement") or ""),
        "AI置信度": str(item.get("confidence") or ""),
        "审核结果": draft.get("manual_review_result", review.get("manual_review_result", "")),
        "审核备注": draft.get("manual_note", review.get("manual_note", "")),
        "查看详情": bool(draft.get("show_detail", False)),
    }


def apply_editor_changes(rows: list[dict[str, Any]], edited_rows: list[dict[str, Any]], drafts: dict[str, dict[str, Any]], state_items: dict[str, Any]) -> set[str]:
    dirty: set[str] = set()
    valid_ids = {str(row["row_id"]) for row in rows}
    for edited in edited_rows:
        row_id = str(edited.get("row_id") or "")
        if row_id not in valid_ids:
            continue
        review = state_items.get(row_id, {})
        result = str(edited.get("审核结果") or "")
        note = str(edited.get("审核备注") or "")
        drafts[row_id] = {"manual_review_result": result, "manual_note": note, "show_detail": bool(edited.get("查看详情"))}
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
