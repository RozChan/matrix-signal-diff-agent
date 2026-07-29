from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bot_task_store import ensure_feishu_meta
from core.confluence_task_store import add_sources, update_source
from core.progress_card import build_task_progress_snapshot, render_task_progress_card, sync_task_progress_card
from core.review_store import create_task_meta, load_task_meta, update_task_meta


class CardClient:
    def __init__(self, fail_update: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.fail_update = fail_update

    def send_progress_card(self, card: dict, *, chat_id: str | None = None, open_id: str | None = None) -> str:
        msg_id = f"card_{len(self.sent) + 1}"
        self.sent.append({"message_id": msg_id, "card": card, "chat_id": chat_id, "open_id": open_id})
        return msg_id

    def update_progress_card(self, message_id: str, card: dict) -> None:
        if self.fail_update:
            raise self.fail_update
        self.updated.append((message_id, card))


def _task(tmp_path: Path, task_id: str = "task") -> Path:
    tdir = tmp_path / task_id
    (tdir / "input" / "4.0").mkdir(parents=True)
    (tdir / "input" / "5.1").mkdir(parents=True)
    create_task_meta(tdir, task_id, status="created")
    ensure_feishu_meta(tdir, sender_id="ou_user", chat_id="oc_chat")
    return tdir


def test_new_task_sends_one_progress_card_and_saves_message_id(tmp_path: Path) -> None:
    tdir = _task(tmp_path)
    client = CardClient()
    assert sync_task_progress_card(tdir, client, force=True)
    assert len(client.sent) == 1
    assert load_task_meta(tdir)["feishu_progress_message_id"] == "card_1"
    assert client.sent[0]["chat_id"] == "oc_chat"


def test_subsequent_progress_updates_use_same_card(tmp_path: Path) -> None:
    tdir = _task(tmp_path)
    client = CardClient()
    sync_task_progress_card(tdir, client, force=True)
    for idx in range(10):
        update_task_meta(tdir, current_stage="信号级AI辅助复核", stage_progress=10 + idx * 5, ai_completed_signal_count=idx + 1, ai_required_signal_count=10)
        sync_task_progress_card(tdir, client, force=True)
    assert len(client.sent) == 1
    assert len(client.updated) == 10
    assert {message_id for message_id, _ in client.updated} == {"card_1"}


def test_different_tasks_have_independent_message_ids(tmp_path: Path) -> None:
    client = CardClient()
    t1 = _task(tmp_path, "t1")
    t2 = _task(tmp_path, "t2")
    sync_task_progress_card(t1, client, force=True)
    sync_task_progress_card(t2, client, force=True)
    assert load_task_meta(t1)["feishu_progress_message_id"] == "card_1"
    assert load_task_meta(t2)["feishu_progress_message_id"] == "card_2"


def test_same_fingerprint_and_threshold_skip_update(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_PROGRESS_UPDATE_MIN_INTERVAL_SECONDS", "999")
    monkeypatch.setenv("FEISHU_PROGRESS_UPDATE_MIN_PERCENT_DELTA", "10")
    tdir = _task(tmp_path)
    client = CardClient()
    sync_task_progress_card(tdir, client, force=True)
    assert not sync_task_progress_card(tdir, client)
    update_task_meta(tdir, stage_progress=5)
    assert not sync_task_progress_card(tdir, client)
    assert client.updated == []


def test_stage_confluence_ai_awaiting_and_failed_update_card(tmp_path: Path) -> None:
    tdir = _task(tmp_path)
    client = CardClient()
    sync_task_progress_card(tdir, client, force=True)
    update_task_meta(tdir, current_stage="新阶段", stage_progress=2)
    assert sync_task_progress_card(tdir, client)
    add_sources(tdir, [{"version": "4.0", "mode": "current_page", "url": "u1", "status": "pending"}], auto_start=True)
    update_source(tdir, "u1", status="completed")
    assert sync_task_progress_card(tdir, client)
    update_task_meta(tdir, current_stage="信号级AI辅助复核", ai_completed_signal_count=2, ai_required_signal_count=13)
    assert sync_task_progress_card(tdir, client)
    update_task_meta(tdir, status="awaiting_review", review_url="http://review")
    assert sync_task_progress_card(tdir, client)
    assert "http://review" in str(client.updated[-1][1])
    update_task_meta(tdir, status="failed", error="boom")
    assert sync_task_progress_card(tdir, client)
    assert "boom" in str(client.updated[-1][1])


def test_update_failure_records_error_and_missing_message_recreates_once(tmp_path: Path) -> None:
    tdir = _task(tmp_path)
    client = CardClient()
    sync_task_progress_card(tdir, client, force=True)
    failing = CardClient(fail_update=RuntimeError("not found"))
    update_task_meta(tdir, current_stage="阶段2", stage_progress=20)
    assert sync_task_progress_card(tdir, failing)
    assert len(failing.sent) == 1
    assert load_task_meta(tdir)["feishu_progress_message_id"] == "card_1"
    bad = CardClient(fail_update=RuntimeError("temporary"))
    update_task_meta(tdir, current_stage="阶段3", stage_progress=40)
    assert not sync_task_progress_card(tdir, bad)
    assert "temporary" in load_task_meta(tdir)["feishu_progress_update_error"]


def test_snapshot_and_card_include_required_counts(tmp_path: Path) -> None:
    tdir = _task(tmp_path)
    (tdir / "input" / "4.0" / "a.xlsx").write_bytes(b"x")
    (tdir / "input" / "5.1" / "b.xlsx").write_bytes(b"x")
    add_sources(tdir, [
        {"version": "4.0", "mode": "current_page", "url": "u1", "status": "completed"},
        {"version": "5.1", "mode": "current_page", "url": "u2", "status": "failed", "errors": ["bad"]},
    ], auto_start=True)
    snapshot = build_task_progress_snapshot(tdir)
    assert snapshot["confluence_source_count"] == 2
    assert snapshot["confluence_completed_source_count"] == 1
    assert snapshot["confluence_failed_source_count"] == 1
    card = render_task_progress_card(snapshot)
    assert "4.0来源：1 / 1" in str(card)
    assert "5.1来源：0 / 1" in str(card)
