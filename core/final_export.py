"""Export final human-reviewed matrix signal difference results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FINAL_REVIEW_FILENAME = "人工审核后最终差异结果.xlsx"

HEADERS = [
    "来源Sheet",
    "4.0信号名",
    "5.1信号名",
    "差异字段",
    "4.0内容",
    "5.1内容",
    "原始差异点list",
    "AI是否复核",
    "AI判断结果",
    "差异类型",
    "置信度",
    "AI判断理由",
    "AI建议处理方式",
    "人工审核结果",
    "人工备注",
    "是否已审核",
    "审核来源",
    "系统默认结论",
    "系统默认原因",
    "审核时间",
]

SHEET_RULES = [
    ("最终保留差异", "确认真实差异"),
    ("确认可忽略差异", "确认可忽略"),
    ("确认错别字", "确认错别字"),
    ("确认语义一致", "确认语义一致"),
    ("存疑待确认", "存疑待确认"),
    ("未审核", ""),
    ("审核明细全量", None),
]


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    result = review.get("manual_review_result", "") if isinstance(review, dict) else ""
    source = review.get("review_source", "") if isinstance(review, dict) else ""
    if not source and result:
        source = "manual"
    return {
        "manual_review_result": result,
        "manual_note": review.get("manual_note", "") if isinstance(review, dict) else "",
        "reviewed": review.get("reviewed", bool(result)) if isinstance(review, dict) else bool(result),
        "review_source": source,
        "default_review_result": review.get("default_review_result", "") if isinstance(review, dict) else "",
        "default_reason": review.get("default_reason", "") if isinstance(review, dict) else "",
        "reviewed_at": review.get("reviewed_at", "") if isinstance(review, dict) else "",
    }


def _source_label(source: str) -> str:
    if source == "system_default":
        return "系统默认"
    if source == "manual":
        return "人工修改"
    return "待人工确认"


def _row_for(item: dict[str, Any], review: dict[str, Any]) -> list[Any]:
    normalized = _normalize_review(review)
    result = normalized["manual_review_result"]
    reviewed = "是" if normalized["reviewed"] else "否"
    return [
        item.get("source_sheet", ""),
        item.get("signal_40", ""),
        item.get("signal_51", ""),
        item.get("diff_field", ""),
        item.get("value_40", ""),
        item.get("value_51", ""),
        item.get("original_diff_list", ""),
        item.get("ai_reviewed", ""),
        item.get("ai_judgement", ""),
        item.get("difference_type", ""),
        item.get("confidence", ""),
        item.get("ai_reason", ""),
        item.get("ai_suggested_action", ""),
        result,
        normalized["manual_note"],
        reviewed,
        _source_label(normalized["review_source"]),
        normalized["default_review_result"],
        normalized["default_reason"],
        normalized["reviewed_at"],
    ]


def _style_sheet(ws) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = [22, 34, 34, 16, 48, 48, 70, 12, 16, 18, 12, 52, 18, 18, 40, 12, 14, 18, 54, 24]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _stats_for(items: list[dict[str, Any]], state_items: dict[str, Any]) -> dict[str, int]:
    stats = {
        "total": len(items),
        "confirmed_real_diff": 0,
        "ignored": 0,
        "typo": 0,
        "semantic_same": 0,
        "uncertain": 0,
        "unreviewed": 0,
    }
    for item in items:
        result = _normalize_review(state_items.get(item.get("item_id"), {}))["manual_review_result"]
        if result == "确认真实差异":
            stats["confirmed_real_diff"] += 1
        elif result == "确认可忽略":
            stats["ignored"] += 1
        elif result == "确认错别字":
            stats["typo"] += 1
        elif result == "确认语义一致":
            stats["semantic_same"] += 1
        elif result == "存疑待确认":
            stats["uncertain"] += 1
        else:
            stats["unreviewed"] += 1
    return stats


def export_final_review_result(review_items_path: Path, review_state_path: Path, output_file_path: Path) -> dict[str, int]:
    items = _load_json(Path(review_items_path))
    state = _load_json(Path(review_state_path)) if Path(review_state_path).exists() else {"items": {}}
    state_items = state.get("items", {})

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    all_rows = [(item, _normalize_review(state_items.get(item.get("item_id"), {}))) for item in items]
    for sheet_name, result_filter in SHEET_RULES:
        ws = wb.create_sheet(sheet_name)
        ws.append(HEADERS)
        for item, review in all_rows:
            result = review.get("manual_review_result", "")
            if result_filter is None or result == result_filter or (result_filter == "" and not result):
                ws.append(_row_for(item, review))
        _style_sheet(ws)

    output_path = Path(output_file_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    wb.close()
    return _stats_for(items, state_items)
