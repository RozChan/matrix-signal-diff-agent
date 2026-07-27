"""Export field-level binary human review results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .review_store import iter_item_fields

FINAL_REVIEW_FILENAME = "人工审核后最终差异结果.xlsx"

HEADERS = [
    "来源Sheet", "EEA4.0信号名", "EEA5.1信号名", "差异字段", "EEA4.0字段值", "EEA5.1字段值",
    "AI判断结果", "AI判断理由", "确认结果", "判定来源", "是否已确认", "确认人", "确认时间", "字段标识", "信号标识",
]
SHEET_RULES = ["人工确认不同", "人工确认相同", "系统判定不同", "待人工确认", "审核明细全量"]


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _field_label(field_name: str, result: str) -> str:
    if not result:
        return "待人工确认"
    return f"{field_name}{'相同' if result == 'same' else '不同'}"


def _category(review: dict[str, Any]) -> str:
    if not review.get("reviewed"):
        return "待人工确认"
    if review.get("decision_source") == "system_default":
        return "系统判定不同"
    return "人工确认相同" if review.get("result") == "same" else "人工确认不同"


def _rows(items: list[dict[str, Any]], state_items: dict[str, Any]):
    for item in items:
        reviews = state_items.get(item.get("item_id"), {}).get("field_reviews", {})
        for diff in iter_item_fields(item):
            review = reviews.get(diff["field_key"], {})
            result = str(review.get("result") or "")
            source = str(review.get("decision_source") or "")
            yield _category(review), [
                item.get("source_sheet", ""), item.get("signal_40", ""), item.get("signal_51", ""), diff.get("diff_field", ""),
                diff.get("value_40", ""), diff.get("value_51", ""), item.get("signal_ai_judgement", ""), item.get("signal_ai_reason", ""),
                _field_label(str(diff.get("diff_field") or "字段"), result),
                "系统" if source == "system_default" else ("历史人工" if source == "history_manual" else ("人工" if source == "manual" else "未确认")),
                "是" if review.get("reviewed") else "否", review.get("reviewer", ""), review.get("reviewed_at", ""),
                diff["field_key"], item.get("item_id", ""),
            ]


def _style_sheet(ws) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D9D9D9")
    for cell in ws[1]:
        cell.fill, cell.font = header_fill, header_font
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = [22, 30, 30, 18, 55, 55, 18, 55, 22, 12, 12, 20, 24, 24, 36]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, index).column_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def export_final_review_result(review_items_path: Path, review_state_path: Path, output_file_path: Path) -> dict[str, int]:
    items = _load_json(review_items_path)
    state = _load_json(review_state_path)
    categorized = list(_rows(items, state.get("items", {})))
    workbook = Workbook()
    workbook.remove(workbook.active)
    stats: dict[str, int] = {}
    for sheet_name in SHEET_RULES:
        ws = workbook.create_sheet(sheet_name)
        ws.append(HEADERS)
        selected = categorized if sheet_name == "审核明细全量" else [row for category, row in categorized if category == sheet_name]
        for entry in selected:
            ws.append(entry[1] if sheet_name == "审核明细全量" else entry)
        _style_sheet(ws)
        stats[sheet_name] = len(selected)
    output_path = Path(output_file_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return stats
