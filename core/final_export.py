"""Export the reviewed result on top of the original two-sheet comparison."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .ai_review import DIFF_LIST_HEADER, SIGNAL_40_HEADER, SIGNAL_51_HEADER, SOURCE_SHEETS
from .pipeline import OUTPUT_FILENAMES
from .review_store import HISTORY_MANUAL_SOURCE, MANUAL_SOURCE, iter_item_fields


FINAL_REVIEW_FILENAME = "人工审核后最终差异结果.xlsx"
RESULT_HEADER = "判断结果"
SOURCE_HEADER = "判断来源"
FINAL_RESULT_HEADERS = (RESULT_HEADER, SOURCE_HEADER)
# Backward-compatible name used by older diagnostics; final delivery now keeps
# exactly the original two comparison sheets.
SHEET_RULES = list(SOURCE_SHEETS)


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _item_decision(item: dict[str, Any], state_items: dict[str, Any]) -> tuple[str, str]:
    item_id = str(item.get("item_id") or "")
    reviews = dict((state_items.get(item_id) or {}).get("field_reviews") or {})
    results: list[str] = []
    sources: list[str] = []
    for diff in iter_item_fields(item):
        field_key = str(diff.get("field_key") or "")
        review = dict(reviews.get(field_key) or {})
        result = str(review.get("result") or "")
        if not review.get("reviewed") or result not in {"same", "different"}:
            raise ValueError(f"审核项尚未完成：{item_id}::{field_key}")
        results.append(result)
        sources.append(str(review.get("decision_source") or ""))
    if not results:
        raise ValueError(f"审核项缺少差异字段：{item_id}")
    final_result = "不同" if "different" in results else "相同"
    final_source = "人工" if any(source in {MANUAL_SOURCE, HISTORY_MANUAL_SOURCE} for source in sources) else "系统"
    return final_result, final_source


def _decision_queues(items: list[dict[str, Any]], state_items: dict[str, Any]) -> dict[tuple[str, str, str], deque[tuple[str, str]]]:
    decisions: dict[tuple[str, str, str], deque[tuple[str, str]]] = defaultdict(deque)
    for item in items:
        key = (
            str(item.get("source_sheet") or ""),
            str(item.get("signal_40") or ""),
            str(item.get("signal_51") or ""),
        )
        decisions[key].append(_item_decision(item, state_items))
    return decisions


def _header_map(ws) -> dict[str, int]:
    return {
        str(cell.value).strip(): index
        for index, cell in enumerate(ws[1], start=1)
        if cell.value is not None
    }


def _ensure_result_columns(ws) -> tuple[int, int]:
    headers = _header_map(ws)
    missing = [name for name in (DIFF_LIST_HEADER, SIGNAL_40_HEADER, SIGNAL_51_HEADER) if name not in headers]
    if missing:
        raise ValueError(f"Sheet {ws.title} 缺少列：{'、'.join(missing)}")
    result_col = headers.get(RESULT_HEADER)
    source_col = headers.get(SOURCE_HEADER)
    if result_col and source_col:
        return result_col, source_col
    if result_col or source_col:
        raise ValueError(f"Sheet {ws.title} 的判断列结构不完整")
    insert_at = headers[DIFF_LIST_HEADER] + 1
    ws.insert_cols(insert_at, amount=2)
    ws.cell(1, insert_at, RESULT_HEADER)
    ws.cell(1, insert_at + 1, SOURCE_HEADER)
    return insert_at, insert_at + 1


def _style_result_columns(ws, result_col: int, source_col: int) -> None:
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_styles = {
        result_col: PatternFill("solid", fgColor="C65911"),
        source_col: PatternFill("solid", fgColor="548235"),
    }
    body_styles = {
        result_col: PatternFill("solid", fgColor="FCE4D6"),
        source_col: PatternFill("solid", fgColor="E2F0D9"),
    }
    for column in (result_col, source_col):
        header = ws.cell(1, column)
        header.fill = header_styles[column]
        header.font = Font(bold=True, color="FFFFFF")
        header.border = border
        header.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for row in range(2, (ws.max_row or 1) + 1):
            cell = ws.cell(row, column)
            cell.fill = body_styles[column]
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[ws.cell(1, result_col).column_letter].width = 12
    ws.column_dimensions[ws.cell(1, source_col).column_letter].width = 12
    ws.auto_filter.ref = ws.dimensions


def _source_compare_path(output_path: Path, compare_file_path: Path | None) -> Path:
    source = Path(compare_file_path) if compare_file_path is not None else output_path.parent / OUTPUT_FILENAMES["compare"]
    if not source.is_file():
        raise FileNotFoundError(f"原始同名信号差异文件不存在：{source}")
    if source.resolve() == output_path.resolve():
        raise ValueError("最终结果文件不能覆盖原始同名信号差异文件")
    return source


def export_final_review_result(
    review_items_path: Path,
    review_state_path: Path,
    output_file_path: Path,
    *,
    compare_file_path: Path | None = None,
) -> dict[str, int]:
    """Copy the source comparison, remove the AI sheet and append final decisions."""

    items = _load_json(review_items_path)
    state = _load_json(review_state_path)
    decisions = _decision_queues(items, dict(state.get("items") or {}))
    output_path = Path(output_file_path)
    source_path = _source_compare_path(output_path, compare_file_path)
    workbook = load_workbook(source_path)
    try:
        missing_sheets = [name for name in SOURCE_SHEETS if name not in workbook.sheetnames]
        if missing_sheets:
            raise ValueError(f"原始同名信号差异文件缺少Sheet：{'、'.join(missing_sheets)}")
        for sheet_name in list(workbook.sheetnames):
            if sheet_name not in SOURCE_SHEETS:
                del workbook[sheet_name]

        stats = {"判断结果-相同": 0, "判断结果-不同": 0, "判断来源-人工": 0, "判断来源-系统": 0, "审核信号总数": 0}
        for sheet_name in SOURCE_SHEETS:
            ws = workbook[sheet_name]
            result_col, source_col = _ensure_result_columns(ws)
            headers = _header_map(ws)
            for row in range(2, (ws.max_row or 1) + 1):
                key = (
                    sheet_name,
                    str(ws.cell(row, headers[SIGNAL_40_HEADER]).value or "").strip(),
                    str(ws.cell(row, headers[SIGNAL_51_HEADER]).value or "").strip(),
                )
                queue = decisions.get(key)
                if not queue:
                    raise ValueError(f"原始差异行未找到审核结论：{sheet_name} 第{row}行 {key[1]} / {key[2]}")
                result, source = queue.popleft()
                ws.cell(row, result_col, result)
                ws.cell(row, source_col, source)
                stats[f"判断结果-{result}"] += 1
                stats[f"判断来源-{source}"] += 1
                stats["审核信号总数"] += 1
            _style_result_columns(ws, result_col, source_col)

        remaining = sum(len(queue) for queue in decisions.values())
        if remaining:
            raise ValueError(f"有 {remaining} 条审核结论未能匹配原始差异行")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
        return stats
    finally:
        workbook.close()
