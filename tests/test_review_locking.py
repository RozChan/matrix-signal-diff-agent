from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.review_store import (
    ReviewConflictError,
    ReviewLockError,
    acquire_review_lock,
    begin_final_generation,
    create_task_meta,
    init_review_state,
    load_review_state,
    load_task_meta,
    update_review_field,
    update_task_meta,
)


def _item(item_id: str) -> dict:
    return {
        "item_id": item_id,
        "source_sheet": "sheet",
        "signal_40": f"S40_{item_id}",
        "signal_51": f"S51_{item_id}",
        "field_diffs": [{"diff_field": "信号值描述", "value_40": "a", "value_51": "b", "field_type": "text"}],
        "signal_ai_judgement": "无法判断",
    }


def _task(tmp_path: Path, task_id: str = "task") -> tuple[Path, Path, list[dict]]:
    task_dir = tmp_path / task_id
    review_dir = task_dir / "review"
    review_dir.mkdir(parents=True)
    create_task_meta(task_dir, task_id, status="reviewing")
    items = [_item("a"), _item("b")]
    init_review_state(review_dir, task_id, items)
    return task_dir, review_dir, items


def test_two_sessions_contend_lock_only_one_succeeds(tmp_path: Path) -> None:
    task_dir, _, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1", owner="alice")
    with pytest.raises(ReviewLockError):
        acquire_review_lock(task_dir, "s2", owner="bob")
    meta = load_task_meta(task_dir)
    assert meta["review_session_id"] == "s1"
    assert meta["review_owner"] == "alice"


def test_different_task_ids_have_independent_locks(tmp_path: Path) -> None:
    task_a, _, _ = _task(tmp_path, "a")
    task_b, _, _ = _task(tmp_path, "b")
    acquire_review_lock(task_a, "s1", owner="alice")
    acquire_review_lock(task_b, "s2", owner="bob")
    assert load_task_meta(task_a)["review_session_id"] == "s1"
    assert load_task_meta(task_b)["review_session_id"] == "s2"


def test_non_lock_holder_cannot_save(tmp_path: Path) -> None:
    task_dir, review_dir, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "owner")
    with pytest.raises(ReviewLockError):
        update_review_field(review_dir, "task", "a", "信号值描述", "different", session_id="other")


def test_old_revision_cannot_overwrite_newer_state(tmp_path: Path) -> None:
    task_dir, review_dir, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    base = load_review_state(review_dir)["revision"]
    update_review_field(review_dir, "task", "a", "信号值描述", "different", reviewer="new", base_revision=base, session_id="s1")
    with pytest.raises(ReviewConflictError):
        update_review_field(review_dir, "task", "b", "信号值描述", "same", reviewer="stale", base_revision=base, session_id="s1")
    state = load_review_state(review_dir)
    assert state["items"]["a"]["field_reviews"]["信号值描述"]["reviewer"] == "new"
    assert state["items"]["b"]["field_reviews"]["信号值描述"]["result"] == ""


def test_current_revision_item_updates_do_not_lose_other_items(tmp_path: Path) -> None:
    task_dir, review_dir, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    update_review_field(review_dir, "task", "a", "信号值描述", "different", reviewer="first", base_revision=0, session_id="s1")
    update_review_field(review_dir, "task", "b", "信号值描述", "same", reviewer="second", base_revision=1, session_id="s1")
    state = load_review_state(review_dir)
    assert state["revision"] == 2
    assert state["items"]["a"]["field_reviews"]["信号值描述"]["reviewer"] == "first"
    assert state["items"]["b"]["field_reviews"]["信号值描述"]["reviewer"] == "second"


def test_concurrent_different_item_updates_without_browser_revision_do_not_lose_data(tmp_path: Path) -> None:
    task_dir, review_dir, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    errors: list[Exception] = []

    def save(item_id: str, note: str) -> None:
        try:
            update_review_field(review_dir, "task", item_id, "信号值描述", "different", reviewer=note, session_id="s1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [Thread(target=save, args=("a", "first")), Thread(target=save, args=("b", "second"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    state = load_review_state(review_dir)
    assert state["items"]["a"]["field_reviews"]["信号值描述"]["reviewer"] == "first"
    assert state["items"]["b"]["field_reviews"]["信号值描述"]["reviewer"] == "second"


def test_two_finish_clicks_only_one_begins_generation(tmp_path: Path) -> None:
    task_dir, _, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    begin_final_generation(task_dir, "s1")
    with pytest.raises(ReviewLockError):
        begin_final_generation(task_dir, "s1")
    meta = load_task_meta(task_dir)
    assert meta["review_completed"] is True
    assert meta["final_generation_status"] == "generating"


def test_final_generation_sets_single_delivery_pending_after_export_guard(tmp_path: Path) -> None:
    task_dir, _, _ = _task(tmp_path)
    update_task_meta(task_dir, source="feishu_confluence")
    acquire_review_lock(task_dir, "s1")
    begin_final_generation(task_dir, "s1")
    update_task_meta(task_dir, status="final_exported", final_generation_status="done", result_delivery_status="pending")
    with pytest.raises(ReviewLockError):
        begin_final_generation(task_dir, "s1")
    assert load_task_meta(task_dir)["result_delivery_status"] == "pending"


def test_expired_lock_can_be_acquired_by_another_session(tmp_path: Path) -> None:
    task_dir, _, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    update_task_meta(task_dir, review_lock_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"))
    acquire_review_lock(task_dir, "s2")
    assert load_task_meta(task_dir)["review_session_id"] == "s2"


def test_completed_review_cannot_reacquire_lock(tmp_path: Path) -> None:
    task_dir, _, _ = _task(tmp_path)
    acquire_review_lock(task_dir, "s1")
    begin_final_generation(task_dir, "s1")
    with pytest.raises(ReviewLockError):
        acquire_review_lock(task_dir, "s2", takeover=True)
