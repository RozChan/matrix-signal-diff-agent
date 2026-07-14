from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import bot_service
from core.confluence_task_store import add_sources, update_source
from core.lark_cli_client import LarkCliClient, LarkCliError
from core.result_notifier import notify_review_ready, scan_and_notify
from core.review_store import create_task_meta, load_task_meta, update_task_meta
from core.final_export import FINAL_REVIEW_FILENAME
from core.result_notifier import deliver_results
from tools import retry_result_delivery, retry_task_notification


class RecordingClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []
        self.status_seen_during_send: list[str] = []

    def send_text(self, user_id: str | None = None, text: str | None = None, *, chat_id: str | None = None) -> str | None:
        self.status_seen_during_send.append(load_task_meta(self.task_dir).get("notification_status", ""))  # type: ignore[attr-defined]
        if self.fail:
            raise RuntimeError("send boom")
        self.sent.append((chat_id or user_id or "", text or ""))
        return f"msg_{len(self.sent)}"

    def send_file(self, user_id: str | None = None, file_path: Path | None = None, *, chat_id: str | None = None, timeout: int | None = None) -> str | None:
        if self.fail:
            raise RuntimeError("file boom")
        self.sent.append((chat_id or user_id or "", f"FILE:{Path(file_path or "").name}:{timeout}"))
        return f"file_{len(self.sent)}"


def make_task(tmp_path: Path, task_id: str = "task_notice") -> Path:
    tdir = tmp_path / task_id
    (tdir / "input" / "4.0").mkdir(parents=True)
    (tdir / "input" / "5.1").mkdir(parents=True)
    create_task_meta(tdir, task_id, status="awaiting_review")
    update_task_meta(
        tdir,
        source="feishu_confluence",
        feishu_chat_id="oc_chat1",
        feishu_sender_id="user1",
        review_url=f"http://localhost:8501/?task_id={task_id}&token=tok",
        notification_status="pending",
        notification_error="",
        input_40_count=2,
        input_51_count=2,
        signal_total=2,
    )
    return tdir


class CapturingLarkClient(LarkCliClient):
    def __init__(self) -> None:
        super().__init__("lark-cli")
        self.commands: list[tuple[str, ...]] = []
    def run_cli(self, *args: str, timeout=None, expect_json=False):
        self.commands.append(args)
        return {"data": {"message_id": "msg"}} if expect_json else "ok"


def test_lark_send_text_uses_chat_id_for_oc() -> None:
    client = CapturingLarkClient()
    assert client.send_text(chat_id="oc_chat", user_id="ou_user", text="hello") == "msg"
    assert "--chat-id" in client.commands[0]
    assert "oc_chat" in client.commands[0]
    assert "--user-id" not in client.commands[0]


def test_lark_send_text_uses_user_id_for_ou_without_chat() -> None:
    client = CapturingLarkClient()
    assert client.send_text(user_id="ou_user", text="hello") == "msg"
    assert "--user-id" in client.commands[0]
    assert "ou_user" in client.commands[0]


def test_lark_send_text_rejects_mixed_or_invalid_prefixes() -> None:
    client = CapturingLarkClient()
    with pytest.raises(LarkCliError):
        client.send_text(user_id="oc_wrong", text="hello")
    with pytest.raises(LarkCliError):
        client.send_text(chat_id="ou_wrong", text="hello")
    assert client.commands == []


def test_notify_review_ready_sends_link_and_sets_sent(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "20260714_132504_f0bb2f")
    client = RecordingClient()
    client.task_dir = tdir
    assert notify_review_ready(client, tdir)
    meta = load_task_meta(tdir)
    assert meta["notification_status"] == "sent"
    assert meta["notification_error"] == ""
    assert meta.get("notification_sent_at")
    assert client.status_seen_during_send == ["sending"]
    recipient, text = client.sent[0]
    assert recipient == "oc_chat1"
    assert "任务编号：20260714_132504_f0bb2f" in text
    assert "4.0输入文件：2个" in text
    assert "5.1输入文件：2个" in text
    assert "待审核信号：2个" in text
    assert "http://localhost:8501/?task_id=20260714_132504_f0bb2f&token=tok" in text


def test_notify_review_ready_failure_sets_failed_and_error(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    client = RecordingClient(fail=True)
    client.task_dir = tdir
    assert not notify_review_ready(client, tdir)
    meta = load_task_meta(tdir)
    assert meta["notification_status"] == "failed"
    assert "send boom" in meta["notification_error"]


def test_sent_notification_is_idempotent_and_concurrent(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    client = RecordingClient()
    client.task_dir = tdir
    threads = [threading.Thread(target=notify_review_ready, args=(client, tdir)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(client.sent) == 1
    assert load_task_meta(tdir)["notification_status"] == "sent"
    assert notify_review_ready(client, tdir)
    assert len(client.sent) == 1


def test_notify_review_ready_missing_url_records_error(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    update_task_meta(tdir, review_url="")
    client = RecordingClient()
    client.task_dir = tdir
    assert not notify_review_ready(client, tdir)
    assert client.sent == []
    meta = load_task_meta(tdir)
    assert meta["notification_status"] == "failed"
    assert "review_url缺失" in meta["notification_error"]


def test_scan_and_notify_resends_pending_but_not_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    pending = make_task(tmp_path, "pending_task")
    sent = make_task(tmp_path, "sent_task")
    update_task_meta(sent, notification_status="sent")
    client = RecordingClient()
    client.task_dir = pending
    scan_and_notify(client)
    assert len(client.sent) == 1
    assert load_task_meta(pending)["notification_status"] == "sent"
    assert load_task_meta(sent)["notification_status"] == "sent"


def test_worker_failed_sends_failure_notification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    tdir = make_task(tmp_path)
    update_task_meta(tdir, status="failed", error="pipeline boom", notification_status="pending")
    client = RecordingClient()
    client.task_dir = tdir
    scan_and_notify(client)
    assert len(client.sent) == 1
    assert "任务失败" in client.sent[0][1]
    assert "pipeline boom" in client.sent[0][1]


def test_worker_monitor_runs_wait_in_background(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tdir = tmp_path / "task_bg"
    (tdir / "input" / "4.0").mkdir(parents=True)
    (tdir / "input" / "5.1").mkdir(parents=True)
    (tdir / "input" / "4.0" / "a.xlsx").write_text("x")
    (tdir / "input" / "5.1" / "b.xlsx").write_text("x")
    create_task_meta(tdir, "task_bg", status="created")
    update_task_meta(tdir, source="feishu_confluence", feishu_chat_id="oc_chat1", notification_status="pending")
    srcs = [
        {"version": "4.0", "mode": "current_page", "url": "u1", "status": "completed"},
        {"version": "5.1", "mode": "current_page", "url": "u2", "status": "completed"},
    ]
    add_sources(tdir, srcs, auto_start=True)
    done = threading.Event()

    class BlockingProcess:
        def wait(self) -> int:
            done.wait(2)
            update_task_meta(tdir, status="awaiting_review", review_url="http://review", signal_total=2)
            return 0

    monkeypatch.setattr(bot_service, "_start_worker", lambda task_id, enable_ai=True: BlockingProcess())
    client = RecordingClient()
    client.task_dir = tdir
    started_at = time.monotonic()
    assert bot_service._start_ready_task("task_bg", tdir, client, "chat1")
    assert time.monotonic() - started_at < 0.5
    assert client.sent == [("chat1", "Confluence文件下载完成：\n\n4.0文件：1个\n5.1文件：1个\n\n正在自动开始信号矩阵差异识别。")]
    done.set()
    deadline = time.time() + 2
    while time.time() < deadline and len(client.sent) < 2:
        time.sleep(0.05)
    assert len(client.sent) == 2
    assert "请点击以下链接进入人工审核" in client.sent[1][1]


def test_failed_notification_does_not_retry_within_backoff(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    client = RecordingClient(fail=True)
    client.task_dir = tdir
    assert not notify_review_ready(client, tdir)
    first = load_task_meta(tdir)
    assert first["notification_status"] == "failed"
    assert first.get("notification_next_retry_at")
    assert not notify_review_ready(client, tdir)
    second = load_task_meta(tdir)
    assert second["notification_retry_count"] == first["notification_retry_count"]


def test_invalid_task_ids_do_not_call_sender(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    update_task_meta(tdir, feishu_chat_id="ou_not_chat", feishu_sender_id="")
    client = RecordingClient()
    client.task_dir = tdir
    assert not notify_review_ready(client, tdir)
    assert client.sent == []
    meta = load_task_meta(tdir)
    assert meta["notification_status"] == "failed"
    assert "oc_" in meta["notification_error"]


def test_retry_task_notification_only_resends_notice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    tdir = make_task(tmp_path, "retry_me")
    client = RecordingClient()
    client.task_dir = tdir

    class FakeCli:
        def __init__(self, cli_path=None):
            pass
        def send_text(self, user_id: str | None = None, text: str | None = None, *, chat_id: str | None = None) -> str:
            return client.send_text(user_id=user_id, chat_id=chat_id, text=text) or "msg"

    monkeypatch.setattr(retry_task_notification, "LarkCliClient", FakeCli)
    assert retry_task_notification.main(["--task-id", "retry_me"]) == 0
    assert len(client.sent) == 1
    assert load_task_meta(tdir)["notification_status"] == "sent"
    assert retry_task_notification.main(["--task-id", "retry_me"]) == 0
    assert len(client.sent) == 1


def test_deliver_results_sends_files_to_chat_and_marks_delivered(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "deliver_me")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(tdir, status="final_exported", result_delivery_status="pending")
    client = RecordingClient()
    client.task_dir = tdir
    assert deliver_results(client, tdir, load_task_meta(tdir))
    meta = load_task_meta(tdir)
    assert meta["status"] == "delivered"
    assert meta["result_delivery_status"] == "delivered"
    assert meta["delivery_error"] == ""
    assert any(target == "oc_chat1" and text.startswith("FILE:") for target, text in client.sent)
    assert any("最终结果文件已发送" in text for _, text in client.sent)


def test_deliver_results_failure_sets_failed_and_error(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "deliver_fail")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(tdir, status="final_exported", result_delivery_status="pending")
    client = RecordingClient(fail=True)
    client.task_dir = tdir
    assert not deliver_results(client, tdir, load_task_meta(tdir))
    meta = load_task_meta(tdir)
    assert meta["result_delivery_status"] == "failed"
    assert "file boom" in meta["delivery_error"] or "send boom" in meta["delivery_error"]


def test_deliver_results_missing_or_empty_file_does_not_call_feishu(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "deliver_empty")
    (tdir / "output").mkdir(exist_ok=True)
    update_task_meta(tdir, status="final_exported", result_delivery_status="pending")
    client = RecordingClient()
    client.task_dir = tdir
    assert not deliver_results(client, tdir, load_task_meta(tdir))
    assert client.sent == []
    assert load_task_meta(tdir)["result_delivery_status"] == "failed"


def test_retry_result_delivery_does_not_rerun_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    tdir = make_task(tmp_path, "retry_delivery")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(tdir, status="final_exported", result_delivery_status="pending")
    client = RecordingClient()
    client.task_dir = tdir

    class FakeCli:
        def __init__(self, cli_path=None):
            pass
        def send_text(self, user_id=None, text=None, *, chat_id=None):
            return client.send_text(user_id=user_id, chat_id=chat_id, text=text)
        def send_file(self, user_id=None, file_path=None, *, chat_id=None, timeout=None):
            return client.send_file(user_id=user_id, chat_id=chat_id, file_path=file_path, timeout=timeout)

    monkeypatch.setattr(retry_result_delivery, "LarkCliClient", FakeCli)
    assert retry_result_delivery.main(["--task-id", "retry_delivery"]) == 0
    assert load_task_meta(tdir)["status"] == "delivered"


def test_failed_result_not_due_does_not_send_again(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "old_failed")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(
        tdir,
        status="final_exported",
        result_delivery_status="failed",
        result_delivery_attempt_count=1,
        result_delivery_next_retry_at="2999-01-01T00:00:00+00:00",
        delivery_error="old failure",
    )
    client = RecordingClient()
    client.task_dir = tdir
    for _ in range(100):
        assert not deliver_results(client, tdir, load_task_meta(tdir))
    assert client.sent == []
    assert load_task_meta(tdir)["result_delivery_attempt_count"] == 1


def test_legacy_failed_result_without_retry_fields_is_suspended(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "legacy_failed")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(tdir, status="final_exported", result_delivery_status="failed", delivery_error="old")
    client = RecordingClient()
    client.task_dir = tdir
    assert not deliver_results(client, tdir, load_task_meta(tdir))
    meta = load_task_meta(tdir)
    assert meta["result_delivery_auto_retry_exhausted"] is True
    assert client.sent == []


def test_result_delivery_failure_notice_same_attempt_only_once(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "fail_once")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(tdir, status="final_exported", result_delivery_status="pending")
    client = RecordingClient(fail=True)
    client.task_dir = tdir
    assert not deliver_results(client, tdir, load_task_meta(tdir))
    sent_count = len(client.sent)
    assert not deliver_results(client, tdir, load_task_meta(tdir))
    assert len(client.sent) == sent_count


def test_result_delivery_force_bypasses_exhaustion(tmp_path: Path) -> None:
    tdir = make_task(tmp_path, "force_delivery")
    out = tdir / "output"
    out.mkdir(exist_ok=True)
    (out / FINAL_REVIEW_FILENAME).write_bytes(b"excel")
    update_task_meta(
        tdir,
        status="final_exported",
        result_delivery_status="failed",
        result_delivery_attempt_count=3,
        result_delivery_auto_retry_exhausted=True,
        delivery_error="old",
    )
    client = RecordingClient()
    client.task_dir = tdir
    assert deliver_results(client, tdir, load_task_meta(tdir), force=True)
    assert load_task_meta(tdir)["status"] == "delivered"
