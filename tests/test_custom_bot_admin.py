from __future__ import annotations

import hashlib
import hmac
import base64
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.admin_tasks import admin_token_valid, safe_task_dir
from core.bot_task_store import build_review_url
from core.feishu_custom_bot import FeishuCustomBotClient, generate_signature
from core.notification_router import notify_result_ready, notify_review_ready, notify_task_failed, notify_task_started
from core.result_access import allowed_result_files, ensure_result_access, resolve_allowed_result_file, result_token_valid
from core.review_store import create_task_meta, load_task_meta, update_task_meta


class FakeResponse:
    status_code = 200
    def json(self): return {"code": 0}


class FakeSession:
    def __init__(self): self.calls = []
    def post(self, url, json, timeout): self.calls.append((url, json, timeout)); return FakeResponse()


class FakeCustomClient:
    def __init__(self, fail=False): self.cards = []; self.fail = fail
    def send_card(self, title, markdown, **kwargs):
        if self.fail: raise RuntimeError("webhook unavailable")
        self.cards.append((title, markdown, kwargs))


def task(tmp_path: Path, trigger="email_auto") -> Path:
    tdir = tmp_path / "task1"
    create_task_meta(tdir, "task1")
    update_task_meta(tdir, source="auto_full_compare", trigger_source=trigger, notify_type="feishu_custom_bot", notify_target="default_group", triggered_at="now", current_stage="created")
    return tdir


def test_custom_bot_signature_and_success(monkeypatch) -> None:
    timestamp = 123456
    expected = base64.b64encode(hmac.new(f"{timestamp}\nsecret".encode(), digestmod=hashlib.sha256).digest()).decode()
    assert generate_signature(timestamp, "secret") == expected
    monkeypatch.setenv("FEISHU_CUSTOM_BOT_ENABLED", "true")
    session = FakeSession()
    client = FeishuCustomBotClient("https://example.test/hook/redacted", "secret", session)
    client.send_card("title", "body", button_text="open", button_url="https://intranet/task")
    assert len(session.calls) == 1
    assert session.calls[0][1]["msg_type"] == "interactive"
    assert "sign" in session.calls[0][1]


def test_custom_bot_errors_do_not_expose_credentials(monkeypatch) -> None:
    webhook = "https://example.test/hook/private-value"
    secret = "private-secret"

    class FailingSession:
        def post(self, url, **kwargs):
            raise requests.ConnectionError(f"cannot reach {url}; secret={secret}")

    monkeypatch.setenv("FEISHU_CUSTOM_BOT_MAX_ATTEMPTS", "1")
    client = FeishuCustomBotClient(webhook, secret, FailingSession())
    with pytest.raises(Exception) as error:
        client.send_card("title", "body")
    assert webhook not in str(error.value)
    assert secret not in str(error.value)


def test_started_review_failed_result_notifications_are_idempotent(tmp_path: Path) -> None:
    tdir = task(tmp_path)
    client = FakeCustomClient()
    assert notify_task_started(tdir, custom_client=client)
    assert notify_task_started(tdir, custom_client=client)
    assert len(client.cards) == 1
    update_task_meta(tdir, status="awaiting_review", review_url="https://review/task", input_40_count=2, input_51_count=3, signal_total=4)
    assert notify_review_ready(tdir, custom_client=client)
    assert notify_review_ready(tdir, custom_client=client)
    assert len(client.cards) == 2 and client.cards[-1][2]["button_url"] == "https://review/task"
    update_task_meta(tdir, status="failed", current_stage="download", error="bad")
    assert notify_task_failed(tdir, custom_client=client)
    assert notify_task_failed(tdir, custom_client=client)
    assert len(client.cards) == 3
    output = tdir / "output"
    output.mkdir(parents=True)
    (output / "人工审核后最终差异结果.xlsx").write_bytes(b"xlsx")
    update_task_meta(tdir, status="final_exported", review_completed_at="now")
    assert notify_result_ready(tdir, custom_client=client)
    assert notify_result_ready(tdir, custom_client=client)
    assert len(client.cards) == 4 and "result_token=" in client.cards[-1][2]["button_url"]


def test_notification_failure_is_recorded_without_failing_task(tmp_path: Path) -> None:
    tdir = task(tmp_path)
    assert not notify_task_started(tdir, custom_client=FakeCustomClient(fail=True))
    meta = load_task_meta(tdir)
    assert meta["status"] == "created"
    assert meta["custom_bot_started_status"] == "failed"
    assert "webhook unavailable" in meta["custom_bot_started_last_error"]


def test_enterprise_task_does_not_use_custom_bot(tmp_path: Path) -> None:
    tdir = tmp_path / "enterprise"
    create_task_meta(tdir, "enterprise")
    update_task_meta(tdir, source="feishu", notify_type="user", feishu_sender_id="ou_user")
    client = FakeCustomClient()
    assert not notify_task_started(tdir, custom_client=client)
    assert client.cards == []


def test_admin_token_and_task_path_protection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_PAGE_ACCESS_TOKEN", "correct")
    assert admin_token_valid("correct") and not admin_token_valid("wrong")
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    tdir = tmp_path / "task1"
    create_task_meta(tdir, "task1")
    assert safe_task_dir("task1") == tdir.resolve()
    with pytest.raises(ValueError): safe_task_dir("../secret")
    with pytest.raises(FileNotFoundError): safe_task_dir("missing")


def test_result_token_and_allowlist_prevent_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_BASE_URL", "http://intranet:8501")
    tdir = task(tmp_path)
    output = tdir / "output"
    output.mkdir(parents=True)
    allowed = output / "人工审核后最终差异结果.xlsx"
    allowed.write_bytes(b"result")
    (tmp_path / "secret.txt").write_text("secret")
    meta = ensure_result_access(tdir)
    assert result_token_valid(tdir, meta["result_token"])
    assert not result_token_valid(tdir, "bad")
    assert allowed.resolve() in allowed_result_files(tdir)
    assert resolve_allowed_result_file(tdir, "../secret.txt") is None
    assert resolve_allowed_result_file(tdir, "secret.txt") is None
