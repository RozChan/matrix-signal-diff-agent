from pathlib import Path
import json
import sys

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.final_export import HEADERS, SHEET_RULES, export_final_review_result
from core.ai_review import is_text_only_ai_candidate
from core.review_store import acquire_review_lock, create_task_meta, init_review_state, save_review_state, update_review_field


def _text_item() -> dict:
    return {
        "item_id": "signal-1", "source_sheet": "完全同名匹配对比结果", "signal_40": "S40", "signal_51": "S51",
        "field_diffs": [
            {"diff_field": "信号值描述", "value_40": "Off", "value_51": "Disable", "field_type": "text"},
            {"diff_field": "单位", "value_40": "Nm", "value_51": "N·m", "field_type": "text"},
        ],
        "signal_ai_judgement": "疑似可忽略", "signal_ai_reason": "文本可能等价",
    }


def _numeric_item() -> dict:
    return {
        "item_id": "signal-2", "source_sheet": "完全同名匹配对比结果", "signal_40": "N40", "signal_51": "N51",
        "field_diffs": [
            {"diff_field": "信号值描述", "value_40": "Off", "value_51": "Disable", "field_type": "text"},
            {"diff_field": "信号长度", "value_40": "8", "value_51": "12", "field_type": "numeric"},
        ],
        "signal_ai_judgement": "真实差异", "signal_ai_reason": "包含数值差异",
    }


def test_field_review_updates_existing_value_and_numeric_is_system_different(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    review_dir = task_dir / "review"
    create_task_meta(task_dir, "task", status="reviewing")
    state = init_review_state(review_dir, "task", [_numeric_item()])
    fields = state["items"]["signal-2"]["field_reviews"]
    assert fields["信号长度"]["result"] == "different"
    assert fields["信号长度"]["decision_source"] == "system_default"
    assert fields["信号值描述"]["result"] == "different"
    assert fields["信号值描述"]["decision_source"] == "system_default"

    # Repeated manual confirmation updates the same field instead of adding a record.
    state = init_review_state(review_dir, "task", [_text_item()], overwrite=True)
    acquire_review_lock(task_dir, "session")
    state = update_review_field(review_dir, "task", "signal-1", "信号值描述", "same", base_revision=0, session_id="session")
    state = update_review_field(review_dir, "task", "signal-1", "信号值描述", "different", base_revision=1, session_id="session")
    assert state["items"]["signal-1"]["field_reviews"]["信号值描述"]["result"] == "different"
    assert len(state["items"]["signal-1"]["field_reviews"]) == 2


def test_final_export_has_five_field_level_sheets(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    review_dir = task_dir / "review"
    create_task_meta(task_dir, "task", status="reviewing")
    items = [_text_item(), _numeric_item()]
    init_review_state(review_dir, "task", items)
    acquire_review_lock(task_dir, "session")
    update_review_field(review_dir, "task", "signal-1", "信号值描述", "different", base_revision=0, session_id="session")
    update_review_field(review_dir, "task", "signal-1", "单位", "same", base_revision=1, session_id="session")
    items_path = review_dir / "review_items.json"
    items_path.parent.mkdir(parents=True, exist_ok=True)
    items_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "final.xlsx"
    stats = export_final_review_result(items_path, review_dir / "review_state.json", output)
    assert stats == {"人工确认不同": 1, "人工确认相同": 1, "系统判定不同": 2, "待人工确认": 0, "审核明细全量": 4}
    wb = load_workbook(output, read_only=True)
    try:
        assert wb.sheetnames == SHEET_RULES
        assert [cell.value for cell in wb["审核明细全量"][1]] == HEADERS
        assert wb["人工确认不同"].cell(2, 9).value == "信号值描述不同"
        assert wb["人工确认相同"].cell(2, 9).value == "单位相同"
    finally:
        wb.close()


def test_ai_candidates_are_strictly_description_and_unit_only() -> None:
    assert is_text_only_ai_candidate(_text_item()) is True
    assert is_text_only_ai_candidate(_numeric_item()) is False
    unknown = _text_item()
    unknown["field_diffs"].append({"diff_field": "未解析", "value_40": "", "value_51": "", "field_type": "unknown"})
    assert is_text_only_ai_candidate(unknown) is False


def test_existing_mixed_numeric_state_is_reclassified_without_rerunning_task(tmp_path: Path) -> None:
    review_dir = tmp_path / "task" / "review"
    save_review_state(review_dir, {
        "task_id": "task", "revision": 0, "items": {"signal-2": {"field_reviews": {
            "信号值描述": {"diff_field": "信号值描述", "result": "", "reviewed": False, "decision_source": ""},
            "信号长度": {"diff_field": "信号长度", "result": "different", "reviewed": True, "decision_source": "system_default"},
        }}}
    })
    state = init_review_state(review_dir, "task", [_numeric_item()])
    description = state["items"]["signal-2"]["field_reviews"]["信号值描述"]
    assert description["result"] == "different"
    assert description["decision_source"] == "system_default"
