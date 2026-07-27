from __future__ import annotations

import email
import imaplib
import sys
from datetime import datetime, timedelta, timezone
from email.header import Header
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.full_compare_task import FullCompareBusyError, FullCompareTaskResult
from core.imap_mail_client import ImapMailClient, ImapMailError, MailHeader, decode_mail_header
from core.mail_trigger_store import load_mail_state
from core.mail_watcher import MailWatcher, batch_trigger_id, mail_matches


class FakeMailClient:
    def __init__(self, uids, headers, uidvalidity="77", failures=None):
        self.uids = list(uids)
        self.headers = headers
        self.value = uidvalidity
        self.failures = failures or set()
        self.username = "mailbox@mychery.com"
        self.folder = "INBOX"
        self.closed = False

    def connect(self): pass
    def close(self): self.closed = True
    def uidvalidity(self): return self.value
    def list_uids(self): return list(self.uids)

    def fetch_header(self, uid, uidvalidity):
        if uid in self.failures:
            self.failures.remove(uid)
            raise ImapMailError("temporary")
        return self.headers[uid]


def header(uid: str, subject="矩阵更新", sender="fangyue2@mychery.com", message_id="") -> MailHeader:
    return MailHeader(uid, "77", message_id or f"<{uid}@mail>", sender, "方越", subject, "Mon, 21 Jul 2026 10:00:00 +0800")


def configure(monkeypatch):
    monkeypatch.setenv("MAIL_TRIGGER_INITIAL_BASELINE", "true")
    monkeypatch.setenv("MAIL_TRIGGER_DELAY_SECONDS", "300")
    monkeypatch.setenv("MAIL_TRIGGER_DEBOUNCE_SECONDS", "300")
    monkeypatch.setenv("MAIL_TRIGGER_SENDER_EMAIL", "fangyue2@mychery.com")
    monkeypatch.setenv("MAIL_TRIGGER_SUBJECT_KEYWORD", "更新")


def test_header_decode_filter_and_stable_ids() -> None:
    encoded = Header("EEA矩阵更新", "utf-8").encode()
    assert decode_mail_header(encoded) == "EEA矩阵更新"
    assert mail_matches(header("1", "Re: EEA矩阵更新", "FANGYUE2@MYCHERY.COM"), "fangyue2@mychery.com", "更新")
    assert mail_matches(header("2", "Fw: 矩阵更新"), "fangyue2@mychery.com", "更新")
    assert not mail_matches(header("3", "矩阵发布"), "fangyue2@mychery.com", "更新")
    assert not mail_matches(header("4", "矩阵更新", "other@mychery.com"), "fangyue2@mychery.com", "更新")
    no_id = MailHeader("9", "77", "", "a@b.com", "", "更新", "")
    assert no_id.stable_id("Box@Mail.com", "INBOX") == "imap:box@mail.com:INBOX:77:9"


def test_initial_baseline_and_restart_do_not_trigger_history(tmp_path: Path, monkeypatch) -> None:
    configure(monkeypatch)
    client = FakeMailClient(["1", "2"], {"1": header("1"), "2": header("2")})
    watcher = MailWatcher(client_factory=lambda: client, root=tmp_path)
    first = watcher.poll_once(datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert first["baseline_created"] and not client.headers == {}
    state = load_mail_state(tmp_path)
    assert state["baseline_uids"] == ["1", "2"]
    second = watcher.poll_once(datetime(2026, 7, 21, 0, 1, tzinfo=timezone.utc))
    assert not second["baseline_created"] and second["candidate_count"] == 0


def test_uidvalidity_change_rebuilds_safe_baseline(tmp_path: Path, monkeypatch) -> None:
    configure(monkeypatch)
    first = FakeMailClient(["1"], {"1": header("1")}, "77")
    MailWatcher(client_factory=lambda: first, root=tmp_path).poll_once()
    changed = FakeMailClient(["1", "2", "3"], {}, "88")
    result = MailWatcher(client_factory=lambda: changed, root=tmp_path).poll_once()
    assert result["baseline_created"]
    assert load_mail_state(tmp_path)["baseline_uids"] == ["1", "2", "3"]


def test_new_mail_debounce_batch_and_single_trigger(tmp_path: Path, monkeypatch) -> None:
    configure(monkeypatch)
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    client = FakeMailClient([], {})
    watcher = MailWatcher(client_factory=lambda: client, root=tmp_path)
    watcher.poll_once(now)
    client.uids = ["10"]
    client.headers["10"] = header("10", "更新123", message_id="<stable@mail>")
    assert watcher.poll_once(now + timedelta(seconds=1))["task_id"] == ""
    client.uids.append("12")
    client.headers["12"] = header("12", "Fw: 矩阵更新")
    watcher.poll_once(now + timedelta(seconds=100))
    created = []

    def creator(**kwargs):
        created.append(kwargs)
        return FullCompareTaskResult("task1", tmp_path / "task1", tuple(), False)

    launched = []
    watcher.task_creator = creator
    watcher.launcher = lambda result: launched.append(result.task_id)
    assert watcher.poll_once(now + timedelta(seconds=399))["task_id"] == ""
    assert watcher.poll_once(now + timedelta(seconds=401))["task_id"] == "task1"
    assert len(created) == 1 and launched == ["task1"]
    assert created[0]["trigger_source"] == "email_auto"
    assert created[0]["notify_type"] == "feishu_custom_bot"
    assert created[0]["trigger_metadata"]["mail_batch_size"] == 2
    assert watcher.poll_once(now + timedelta(seconds=800))["task_id"] == ""


def test_intermediate_uid_failure_is_retried_despite_higher_uid(tmp_path: Path, monkeypatch) -> None:
    configure(monkeypatch)
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    client = FakeMailClient([], {})
    watcher = MailWatcher(client_factory=lambda: client, root=tmp_path)
    watcher.poll_once(now)
    client.uids = ["100", "101", "102"]
    client.headers = {uid: header(uid) for uid in client.uids}
    client.failures = {"101"}
    watcher.poll_once(now + timedelta(seconds=1))
    state = load_mail_state(tmp_path)
    assert state["messages"]["77:101"]["status"] == "failed_retryable"
    watcher.poll_once(now + timedelta(seconds=2))
    assert load_mail_state(tmp_path)["messages"]["77:101"]["status"] == "pending"


def test_busy_batch_remains_queued_and_has_finite_attempts(tmp_path: Path, monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv("MAIL_TRIGGER_DELAY_SECONDS", "0")
    monkeypatch.setenv("MAIL_TRIGGER_MAX_QUEUE_ATTEMPTS", "2")
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    client = FakeMailClient([], {})
    watcher = MailWatcher(client_factory=lambda: client, root=tmp_path)
    watcher.poll_once(now)
    client.uids = ["1"]
    client.headers = {"1": header("1")}
    watcher.task_creator = lambda **kwargs: (_ for _ in ()).throw(FullCompareBusyError("busy"))
    watcher.poll_once(now + timedelta(seconds=1))
    assert load_mail_state(tmp_path)["pending_batch"]["status"] == "queued"
    watcher.poll_once(now + timedelta(seconds=62))
    assert load_mail_state(tmp_path)["pending_batch"]["status"] == "failed"


def test_batch_id_is_order_independent() -> None:
    mails = [{"stable_id": "b"}, {"stable_id": "a"}]
    assert batch_trigger_id(mails) == batch_trigger_id(list(reversed(mails)))


def test_imap_client_uses_readonly_uid_and_header_peek(monkeypatch) -> None:
    raw = email.message_from_string("Message-ID: <m@x>\nFrom: =?utf-8?b?5pa56LaK?= <fangyue2@mychery.com>\nSubject: =?utf-8?b?55+p6Zi15pu05paw?=\nDate: now\n\n")
    payload = raw.as_bytes()

    class Connection:
        def login(self, user, password): return "OK", []
        def select(self, folder, readonly=False): self.readonly = readonly; return "OK", []
        def response(self, name): return name, [b"77"]
        def uid(self, command, *args):
            self.last_uid = (command, args)
            if command == "search": return "OK", [b"1"]
            return "OK", [(b"header", payload)]
        def close(self): pass
        def logout(self): pass

    connection = Connection()
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example")
    monkeypatch.setenv("MAIL_IMAP_USERNAME", "user")
    monkeypatch.setenv("MAIL_IMAP_PASSWORD", "secret")
    client = ImapMailClient(connection_factory=lambda host, port: connection)
    client.connect()
    assert connection.readonly is True
    assert client.list_uids() == ["1"]
    parsed = client.fetch_header("1", "77")
    assert parsed.sender_email == "fangyue2@mychery.com" and "更新" in parsed.subject
    assert "BODY.PEEK[HEADER.FIELDS" in connection.last_uid[1][1]
    assert "BODY[TEXT]" not in connection.last_uid[1][1]


def test_imap_login_failure_does_not_expose_password(monkeypatch) -> None:
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example")
    monkeypatch.setenv("MAIL_IMAP_USERNAME", "user")
    monkeypatch.setenv("MAIL_IMAP_PASSWORD", "super-secret-password")

    class Connection:
        def login(self, user, password):
            raise imaplib.IMAP4.error(f"bad password {password}")

    client = ImapMailClient(connection_factory=lambda *args, **kwargs: Connection())
    with pytest.raises(ImapMailError) as exc:
        client.connect()
    assert "super-secret-password" not in str(exc.value)
