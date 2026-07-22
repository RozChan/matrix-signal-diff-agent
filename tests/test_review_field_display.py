from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.review_store import _parse_field_diff_details
from app import _display_text


def test_multiline_field_diff_details_preserve_all_lines() -> None:
    text = """【信号值描述】
4.0：0x0: Engine start enable
0x1: Engine start disable
0x2: Key not stored
0x3: Not valid
5.1：0x0: Engine start enable
0x1: Engine start disable
0x2: SC or SK not stored
0x3: Not valid
类型：文本类差异"""
    [diff] = _parse_field_diff_details(text, ["信号值描述"])
    assert diff["value_40"] == "0x0: Engine start enable\n0x1: Engine start disable\n0x2: Key not stored\n0x3: Not valid"
    assert diff["value_51"] == "0x0: Engine start enable\n0x1: Engine start disable\n0x2: SC or SK not stored\n0x3: Not valid"
    assert "0x2: Key not stored" in diff["value_40"]
    assert "0x2: SC or SK not stored" in diff["value_51"]


def test_multiline_field_diff_details_keep_colons_hyphens_and_newlines() -> None:
    text = """【信号值描述】
4.0：0x0: Not crank
0x3: Crank
5.1：0x0: Not crank
0x1-0x2: Reserved
0x3: Crank
类型：文本类差异"""
    [diff] = _parse_field_diff_details(text, ["信号值描述"])
    assert diff["value_40"] == "0x0: Not crank\n0x3: Crank"
    assert diff["value_51"] == "0x0: Not crank\n0x1-0x2: Reserved\n0x3: Crank"


def test_display_text_handles_empty_none_and_nan() -> None:
    assert _display_text("") == "<空>"
    assert _display_text(None) == "<空>"
    assert _display_text("nan") == "<空>"
    assert _display_text("abc") == "abc"
