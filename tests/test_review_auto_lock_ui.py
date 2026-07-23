from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from core.review_store import acquire_review_lock, begin_final_generation, create_task_meta, init_review_state, load_task_meta, update_task_meta


class FakeColumn:
    def metric(self, *args, **kwargs):
        return None


class FakeSt:
    def __init__(self, *, checkbox_value: bool = False, button_value: bool = False) -> None:
        self.session_state: dict[str, object] = {}
        self.messages: list[tuple[str, str]] = []
        self.buttons: list[str] = []
        self.checkbox_value = checkbox_value
        self.button_value = button_value
        self.rerun_called = False

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def success(self, message: str) -> None:
        self.messages.append(("success", message))

    def warning(self, message: str) -> None:
        self.messages.append(("warning", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))

    def checkbox(self, label: str, **kwargs) -> bool:
        return self.checkbox_value

    def button(self, label: str, **kwargs) -> bool:
        self.buttons.append(label)
        if kwargs.get("disabled"):
            return False
        return self.button_value

    def columns(self, count):
        return [FakeColumn() for _ in range(count if isinstance(count, int) else len(count))]

    def rerun(self) -> None:
        self.rerun_called = True


def _task(tmp_path: Path, task_id: str = "task", status: str = "awaiting_review") -> Path:
    tdir = tmp_path / task_id
    review_dir = tdir / "review"
    review_dir.mkdir(parents=True)
    create_task_meta(tdir, task_id, status=status)
    init_review_state(review_dir, task_id, [{"item_id": "a", "field_diffs": [], "signal_ai_judgement": "无法判断"}])
    return tdir


def test_valid_review_link_auto_acquires_lock_without_start_button(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path)
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    can_edit, session_id, meta = app._show_review_lock_panel(tdir, "task", {"revision": 0})
    assert can_edit is True
    assert meta["review_session_id"] == session_id
    assert not any(label == "开始审核" for label in fake.buttons)
    assert any("已自动进入审核模式" in message for _, message in fake.messages)


def test_same_session_rerun_does_not_reacquire_lock(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path)
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    calls = []
    original = app.acquire_review_lock

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(app, "acquire_review_lock", counted)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is True
    assert len(calls) == 1
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is True
    assert len(calls) == 1


def test_two_sessions_only_one_gets_lock_and_other_is_readonly(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path)
    first = FakeSt()
    monkeypatch.setattr(app, "st", first)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is True
    second = FakeSt()
    monkeypatch.setattr(app, "st", second)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is False
    assert any("只读模式" in message for _, message in second.messages)
    assert not any(label == "开始审核" for label in second.buttons)


def test_different_task_ids_auto_lock_independently(tmp_path: Path, monkeypatch) -> None:
    t1 = _task(tmp_path, "t1")
    t2 = _task(tmp_path, "t2")
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    assert app._show_review_lock_panel(t1, "t1", {"revision": 0})[0] is True
    assert app._show_review_lock_panel(t2, "t2", {"revision": 0})[0] is True
    assert load_task_meta(t1)["review_session_id"] != ""
    assert load_task_meta(t2)["review_session_id"] != ""


def test_expired_lock_is_auto_acquired_but_active_lock_is_not(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path)
    acquire_review_lock(tdir, "old", owner="old")
    update_task_meta(tdir, review_lock_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"))
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is True
    assert any("原审核锁已过期" in message for _, message in fake.messages)

    second = FakeSt()
    monkeypatch.setattr(app, "st", second)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is False


def test_takeover_requires_confirmation_and_records_sessions(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path)
    acquire_review_lock(tdir, "old", owner="alice")
    no_confirm = FakeSt(checkbox_value=False, button_value=True)
    monkeypatch.setattr(app, "st", no_confirm)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is False
    assert load_task_meta(tdir)["review_session_id"] == "old"

    confirmed = FakeSt(checkbox_value=True, button_value=True)
    monkeypatch.setattr(app, "st", confirmed)
    app._show_review_lock_panel(tdir, "task", {"revision": 0})
    meta = load_task_meta(tdir)
    assert meta["review_takeover_from_session"] == "old"
    assert meta["review_takeover_to_session"] == confirmed.session_state["review-session-id-task"]


def test_not_ready_or_completed_task_does_not_auto_acquire(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path, status="running")
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    assert app._show_review_lock_panel(tdir, "task", {"revision": 0})[0] is False
    assert load_task_meta(tdir).get("review_session_id", "") == ""

    ready = _task(tmp_path, "done")
    acquire_review_lock(ready, "owner")
    begin_final_generation(ready, "owner")
    done_st = FakeSt()
    monkeypatch.setattr(app, "st", done_st)
    assert app._show_review_lock_panel(ready, "done", {"revision": 0})[0] is False
    assert any("已完成审核" in message for _, message in done_st.messages)
