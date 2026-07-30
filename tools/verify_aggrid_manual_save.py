"""Verify the formal Manual save adapter against an isolated temporary task."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.review_history import history_counts, history_database_path
from core.review_store import (
    acquire_review_lock,
    compute_review_stats,
    create_task_meta,
    init_review_state,
    load_review_state,
    load_task_meta,
)
from ui import review_table as review_ui
from ui.review_table import field_dirty_state_key, field_drafts_state_key


def review_item() -> dict[str, Any]:
    return {
        "item_id": "formal-row-01",
        "signal_40": "FormalSignal40",
        "signal_51": "FormalSignal51",
        "source_sheet": "完全同名匹配对比结果",
        "diff_fields": ["信号值描述", "单位"],
        "diff_field_count": 2,
        "field_diffs": [
            {
                "diff_field": "信号值描述",
                "value_40": "0x0: Off 0x1: On",
                "value_51": "0x0: Off 0x1: Active",
                "field_type": "text",
            },
            {
                "diff_field": "单位",
                "value_40": "Nm",
                "value_51": "N·m",
                "field_type": "text",
            },
        ],
        "signal_ai_judgement": "疑似可忽略",
    }


def snapshot(items: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    reviews = state["items"]["formal-row-01"]["field_reviews"]
    return {
        "revision": int(state["revision"]),
        "pending_manual_count": int(
            compute_review_stats(items, state)["pending_manual"]
        ),
        "description": {
            "result": reviews["信号值描述"]["result"],
            "reviewed": reviews["信号值描述"]["reviewed"],
        },
        "unit": {
            "result": reviews["单位"]["result"],
            "reviewed": reviews["单位"]["reviewed"],
        },
    }


def run_verification() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="aggrid-manual-formal-") as temp_name:
        root = Path(temp_name)
        task_id = "formal-manual-verification"
        task_dir = root / task_id
        review_dir = task_dir / "review"
        create_task_meta(task_dir, task_id)
        items = [review_item()]
        state = init_review_state(review_dir, task_id, items)
        (review_dir / "review_items.json").write_text(
            json.dumps(items, ensure_ascii=False),
            encoding="utf-8",
        )
        acquire_review_lock(task_dir, "verification-session")

        base_drafts = f"review-drafts-{task_id}"
        base_dirty = f"review-dirty-{task_id}"
        description_drafts = field_drafts_state_key(
            base_drafts,
            "信号值描述",
        )
        description_dirty = field_dirty_state_key(base_dirty, "信号值描述")
        unit_drafts = field_drafts_state_key(base_drafts, "单位")
        unit_dirty = field_dirty_state_key(base_dirty, "单位")
        session_state = {
            description_drafts: {
                "formal-row-01::信号值描述": {
                    "item_id": "formal-row-01",
                    "field_key": "信号值描述",
                    "result": "same",
                }
            },
            description_dirty: ["formal-row-01::信号值描述"],
            unit_drafts: {
                "formal-row-01::单位": {
                    "item_id": "formal-row-01",
                    "field_key": "单位",
                    "result": "different",
                }
            },
            unit_dirty: ["formal-row-01::单位"],
        }
        original_streamlit = review_ui.st
        review_ui.st = SimpleNamespace(session_state=session_state)
        try:
            before = snapshot(items, state)
            after_description_state = review_ui._save_review_changes(
                task_dir,
                review_dir,
                task_id,
                state,
                "verification-session",
                description_drafts,
                description_dirty,
            )
            after_description = snapshot(items, after_description_state)
            unit_state_was_preserved = (
                session_state[unit_dirty] == ["formal-row-01::单位"]
                and session_state[unit_drafts]["formal-row-01::单位"]["result"]
                == "different"
            )
            final_state = review_ui._save_review_changes(
                task_dir,
                review_dir,
                task_id,
                after_description_state,
                "verification-session",
                unit_drafts,
                unit_dirty,
            )
        finally:
            review_ui.st = original_streamlit

        reloaded = load_review_state(review_dir)
        after_unit = snapshot(items, reloaded)
        return {
            "before": before,
            "after_description": after_description,
            "after_unit": after_unit,
            "review_state_reloaded_matches": reloaded == final_state,
            "unit_state_preserved_during_description_submit": (
                unit_state_was_preserved
            ),
            "history": history_counts(
                db_path=history_database_path(review_dir)
            ),
            "task_meta_pending_manual_count": int(
                load_task_meta(task_dir)["pending_manual_count"]
            ),
            "description_drafts_cleared": session_state[description_drafts] == {},
            "unit_drafts_cleared": session_state[unit_drafts] == {},
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = run_verification()
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
