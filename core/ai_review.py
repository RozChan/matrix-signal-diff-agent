"""Generate AI-assisted review and human audit details in the compare workbook."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


from .llm_client import LLMConfigurationError, LLMRequestError, call_chat_json, get_llm_config

SOURCE_SHEETS = ["完全同名匹配对比结果", "vcu-hcu 同名匹配"]
AI_REVIEW_SHEET = "AI辅助复核与人工审核明细"
DIFF_LIST_HEADER = "差异点list"
SIGNAL_40_HEADER = "4.0信号名"
SIGNAL_51_HEADER = "5.1信号名"

REVIEW_HEADERS = [
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
]

TEXT_REVIEW_FIELDS = {"信号值描述", "单位"}
NUMERIC_FIELDS = {"信号长度", "精度", "偏移量", "物理最小值", "物理最大值"}
KNOWN_DIFF_FIELDS = ["信号长度", "精度", "偏移量", "物理最小值", "物理最大值", "单位", "信号值描述"]

ALLOWED_JUDGEMENTS = {"疑似一致", "疑似错别字", "疑似语义相近", "真实差异", "无法判断", "不适用", "未启用"}
ALLOWED_TYPES = {"错别字", "缩写差异", "表达方式不同", "描述粒度不同", "枚举描述相近", "语义不同", "数值字段差异", "不适用"}
ALLOWED_CONFIDENCE = {"高", "中", "低", "不适用"}
ALLOWED_ACTIONS = {"可忽略", "建议人工确认", "应保留差异", "不适用"}

EMPTY_STATS: dict[str, int] = {
    "total_review_items": 0,
    "ai_reviewed_count": 0,
    "ai_skipped_count": 0,
    "suspicious_same_count": 0,
    "typo_count": 0,
    "semantic_similar_count": 0,
    "real_diff_count": 0,
    "unknown_count": 0,
    "not_applicable_count": 0,
    "llm_disabled_count": 0,
}


def _header_map(ws) -> dict[str, int]:
    return {str(cell.value).strip(): idx for idx, cell in enumerate(ws[1], start=1) if cell.value is not None}


def _cell(ws, row_idx: int, headers: dict[str, int], name: str) -> str:
    col = headers.get(name)
    if not col:
        return ""
    value = ws.cell(row=row_idx, column=col).value
    return "" if value is None else str(value).strip()


def parse_diff_list(diff_text: Any) -> list[dict[str, str]]:
    """Parse 差异点list into one item per field difference.

    Falls back to a single 未解析 item instead of raising.
    """

    text = "" if diff_text is None else str(diff_text).strip()
    if not text:
        return []

    fields_pattern = "|".join(re.escape(field) for field in KNOWN_DIFF_FIELDS)
    pattern = re.compile(
        rf"(?P<field>{fields_pattern})：4\.0=(?P<v40>.*?)；5\.1=(?P<v51>.*?)(?=\n(?:{fields_pattern})：4\.0=|\Z)",
        re.S,
    )
    items = [
        {
            "差异字段": match.group("field").strip(),
            "4.0内容": match.group("v40").strip(),
            "5.1内容": match.group("v51").strip(),
        }
        for match in pattern.finditer(text)
    ]
    if items:
        return items
    return [{"差异字段": "未解析", "4.0内容": "", "5.1内容": ""}]


def _safe_choice(value: Any, allowed: set[str], default: str) -> str:
    text = "" if value is None else str(value).strip()
    return text if text in allowed else default


def _stats_increment(stats: dict[str, Any], judgement: str) -> None:
    if judgement == "疑似一致":
        stats["suspicious_same_count"] += 1
    elif judgement == "疑似错别字":
        stats["typo_count"] += 1
    elif judgement == "疑似语义相近":
        stats["semantic_similar_count"] += 1
    elif judgement == "真实差异":
        stats["real_diff_count"] += 1
    elif judgement == "无法判断":
        stats["unknown_count"] += 1
    elif judgement == "不适用":
        stats["not_applicable_count"] += 1
    elif judgement == "未启用":
        stats["llm_disabled_count"] += 1


def _numeric_review() -> dict[str, str]:
    return {
        "AI是否复核": "否",
        "AI判断结果": "不适用",
        "差异类型": "数值字段差异",
        "置信度": "不适用",
        "AI判断理由": "数值类字段差异，未进行 AI 语义复核",
        "AI建议处理方式": "应保留差异",
    }


def _disabled_review() -> dict[str, str]:
    return {
        "AI是否复核": "否",
        "AI判断结果": "未启用",
        "差异类型": "不适用",
        "置信度": "不适用",
        "AI判断理由": "AI辅助复核未启用",
        "AI建议处理方式": "建议人工确认",
    }


def _unknown_review(reason: str) -> dict[str, str]:
    return {
        "AI是否复核": "否",
        "AI判断结果": "无法判断",
        "差异类型": "不适用",
        "置信度": "低",
        "AI判断理由": reason,
        "AI建议处理方式": "建议人工确认",
    }


def _prompt_messages(item: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "你是车辆信号矩阵差异复核助手。你的任务是判断 4.0 和 5.1 的文本类差异是否可能只是"
        "错别字、缩写、表达方式不同或语义相近。你只能基于输入内容判断，不允许编造业务含义。"
        "如果不确定，必须输出“无法判断”。不能把明显不同的含义强行判断为一致。你不能修改原始差异结果。"
        "你的判断只作为人工审核参考。输出必须是 JSON，不要输出 Markdown。"
    )
    user_payload = {
        "来源Sheet": item["来源Sheet"],
        "4.0信号名": item["4.0信号名"],
        "5.1信号名": item["5.1信号名"],
        "差异字段": item["差异字段"],
        "4.0内容": item["4.0内容"],
        "5.1内容": item["5.1内容"],
        "原始差异点list": item["原始差异点list"],
        "输出 JSON 格式": {
            "judgement": "疑似一致/疑似错别字/疑似语义相近/真实差异/无法判断",
            "difference_type": "错别字/缩写差异/表达方式不同/描述粒度不同/枚举描述相近/语义不同/不适用",
            "confidence": "高/中/低",
            "reason": "简短理由",
            "suggested_action": "可忽略/建议人工确认/应保留差异",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _ai_review(item: dict[str, str]) -> dict[str, str]:
    data = call_chat_json(_prompt_messages(item))
    judgement = _safe_choice(data.get("judgement"), ALLOWED_JUDGEMENTS - {"不适用", "未启用"}, "无法判断")
    return {
        "AI是否复核": "是",
        "AI判断结果": judgement,
        "差异类型": _safe_choice(data.get("difference_type"), ALLOWED_TYPES, "不适用"),
        "置信度": _safe_choice(data.get("confidence"), ALLOWED_CONFIDENCE - {"不适用"}, "低"),
        "AI判断理由": str(data.get("reason") or "模型未返回理由").strip(),
        "AI建议处理方式": _safe_choice(data.get("suggested_action"), ALLOWED_ACTIONS - {"不适用"}, "建议人工确认"),
    }


def _review_item(item: dict[str, str], enable_ai: bool, llm_enabled: bool, stats: dict[str, Any]) -> dict[str, str]:
    field = item["差异字段"]
    if field in NUMERIC_FIELDS:
        review = _numeric_review()
    elif field in TEXT_REVIEW_FIELDS:
        if not enable_ai or not llm_enabled:
            review = _disabled_review()
        else:
            try:
                review = _ai_review(item)
            except (LLMConfigurationError, LLMRequestError) as exc:
                warning = str(exc)
                if warning not in stats["warnings"]:
                    stats["warnings"].append(warning)
                review = _unknown_review(f"AI辅助复核失败：{warning}")
    else:
        review = _unknown_review("差异字段未解析或不在 AI 复核范围内")

    stats["total_review_items"] += 1
    if review["AI是否复核"] == "是":
        stats["ai_reviewed_count"] += 1
    else:
        stats["ai_skipped_count"] += 1
    _stats_increment(stats, review["AI判断结果"])
    return review


def _iter_review_base_items(wb) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_sheet in SOURCE_SHEETS:
        if source_sheet not in wb.sheetnames:
            continue
        ws = wb[source_sheet]
        headers = _header_map(ws)
        for row_idx in range(2, (ws.max_row or 1) + 1):
            diff_text = _cell(ws, row_idx, headers, DIFF_LIST_HEADER)
            if not diff_text:
                continue
            base = {
                "来源Sheet": source_sheet,
                "4.0信号名": _cell(ws, row_idx, headers, SIGNAL_40_HEADER),
                "5.1信号名": _cell(ws, row_idx, headers, SIGNAL_51_HEADER),
                "原始差异点list": diff_text,
            }
            for diff in parse_diff_list(diff_text):
                rows.append({**base, **diff})
    return rows


def _style_sheet(ws) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    header_fill = PatternFill("solid", fgColor="7030A0")
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
    widths = {
        "A": 24, "B": 36, "C": 36, "D": 16, "E": 50, "F": 50, "G": 80,
        "H": 12, "I": 16, "J": 18, "K": 12, "L": 55, "M": 18, "N": 18, "O": 40,
    }
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def run_ai_review(compare_file_path: Path, enable_ai: bool = False) -> dict[str, Any]:
    """Append AI辅助复核与人工审核明细 sheet to the compare workbook."""

    compare_path = Path(compare_file_path)
    stats: dict[str, Any] = {**EMPTY_STATS, "warnings": []}
    config = get_llm_config()
    llm_enabled = config.enabled

    from openpyxl import load_workbook

    wb = load_workbook(compare_path)
    if AI_REVIEW_SHEET in wb.sheetnames:
        del wb[AI_REVIEW_SHEET]
    ws = wb.create_sheet(AI_REVIEW_SHEET)
    ws.append(REVIEW_HEADERS)

    for item in _iter_review_base_items(wb):
        review = _review_item(item, enable_ai=enable_ai, llm_enabled=llm_enabled, stats=stats)
        ws.append([
            item["来源Sheet"],
            item["4.0信号名"],
            item["5.1信号名"],
            item["差异字段"],
            item["4.0内容"],
            item["5.1内容"],
            item["原始差异点list"],
            review["AI是否复核"],
            review["AI判断结果"],
            review["差异类型"],
            review["置信度"],
            review["AI判断理由"],
            review["AI建议处理方式"],
            "",
            "",
        ])

    _style_sheet(ws)
    wb.save(compare_path)
    wb.close()
    return stats
