"""Local regression checks for Confluence SSRF and bot auto-start rules.

This script uses fakes only; it does not call real Feishu or Confluence.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.confluence_client import ConfluenceClient, ConfluenceError
from core.confluence_task_store import add_source, update_source
from core.review_store import create_task_meta

import bot_service


class FakeResponse:
    def __init__(self, url: str, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = status_code in {301, 302, 303, 307, 308}
        self.is_permanent_redirect = status_code in {301, 308}

    def json(self) -> dict:
        return {"results": [], "size": 0}


class RedirectSession:
    def __init__(self, location: str) -> None:
        self.headers = {}
        self.location = location
        self.calls: list[str] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append(url)
        return FakeResponse(url, 302, {"Location": self.location})

    def close(self) -> None:
        pass


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_text(self, user_id: str, text: str):
        self.messages.append(text)
        return "msg"


def _set_confluence_env() -> None:
    os.environ["CONFLUENCE_BASE_URL"] = "https://yfconfluence.mychery.com"
    os.environ["CONFLUENCE_PAT"] = "dummy-test-token"
    os.environ["CONFLUENCE_ALLOWED_HOSTS"] = "yfconfluence.mychery.com"


def test_ssrf_host_rules() -> None:
    _set_confluence_env()
    client = ConfluenceClient(session=RedirectSession("https://yfconfluence.mychery.com/pages/viewpage.action?pageId=1"))
    client._validate_allowed_url("https://yfconfluence.mychery.com/pages/viewpage.action?pageId=1")
    for blocked in [
        "https://10.1.2.3/pages/viewpage.action?pageId=1",
        "https://192.168.1.2/pages/viewpage.action?pageId=1",
        "https://172.16.1.2/pages/viewpage.action?pageId=1",
        "https://127.0.0.1/pages/viewpage.action?pageId=1",
        "https://localhost/pages/viewpage.action?pageId=1",
        "https://evil.example.com/pages/viewpage.action?pageId=1",
        "https://user:pass@yfconfluence.mychery.com/pages/viewpage.action?pageId=1",
    ]:
        try:
            client._validate_allowed_url(blocked)
        except ConfluenceError:
            pass
        else:
            raise AssertionError(f"URL should be blocked: {blocked}")


def test_redirect_host_rechecked_before_following() -> None:
    _set_confluence_env()
    session = RedirectSession("https://evil.example.com/pages/viewpage.action?pageId=1")
    client = ConfluenceClient(session=session)
    try:
        client._request("GET", "https://yfconfluence.mychery.com/x/abc")
    except ConfluenceError:
        pass
    else:
        raise AssertionError("cross-host redirect should be blocked")
    assert session.calls == ["https://yfconfluence.mychery.com/x/abc"], session.calls


def _make_task(root: Path) -> tuple[str, Path]:
    task_id = "20260101_000000_test"
    tdir = root / task_id
    (tdir / "input" / "4.0").mkdir(parents=True)
    (tdir / "input" / "5.1").mkdir(parents=True)
    create_task_meta(tdir, task_id, status="created")
    return task_id, tdir


def _add_excel(tdir: Path, version: str, name: str) -> None:
    (tdir / "input" / version / name).write_bytes(b"placeholder")


def test_auto_start_when_all_sources_completed_and_files_ready() -> None:
    os.environ["BOT_AUTO_START_WHEN_BOTH_READY"] = "true"
    with tempfile.TemporaryDirectory() as tmp:
        task_id, tdir = _make_task(Path(tmp))
        _add_excel(tdir, "4.0", "a.xlsx")
        _add_excel(tdir, "5.1", "b.xlsx")
        add_source(tdir, {"version": "4.0", "mode": "current_page", "url": "u1", "status": "completed"})
        add_source(tdir, {"version": "5.1", "mode": "current_page", "url": "u2", "status": "completed"})
        fake = FakeClient()
        calls = []
        old_start = bot_service._start_worker
        bot_service._start_worker = lambda *args, **kwargs: calls.append(args) or SimpleNamespace(pid=1)
        try:
            bot_service._maybe_auto_start(task_id, tdir, fake, "user")
        finally:
            bot_service._start_worker = old_start
        assert len(calls) == 1
        assert any("正在自动开始" in msg for msg in fake.messages)


def test_auto_start_requires_all_sources_completed() -> None:
    os.environ["BOT_AUTO_START_WHEN_BOTH_READY"] = "true"
    with tempfile.TemporaryDirectory() as tmp:
        task_id, tdir = _make_task(Path(tmp))
        _add_excel(tdir, "4.0", "a.xlsx")
        _add_excel(tdir, "5.1", "b.xlsx")
        add_source(tdir, {"version": "4.0", "mode": "current_page", "url": "u1", "status": "completed"})
        add_source(tdir, {"version": "5.1", "mode": "current_page", "url": "u2", "status": "failed", "errors": ["403"]})
        fake = FakeClient()
        calls = []
        old_start = bot_service._start_worker
        bot_service._start_worker = lambda *args, **kwargs: calls.append(args) or SimpleNamespace(pid=1)
        try:
            bot_service._maybe_auto_start(task_id, tdir, fake, "user")
        finally:
            bot_service._start_worker = old_start
        assert not calls, calls
        assert any("重试Confluence下载" in msg and "忽略失败来源并开始处理" in msg for msg in fake.messages)


def test_ignore_failed_explicitly_can_start_when_files_ready() -> None:
    os.environ["BOT_AUTO_START_WHEN_BOTH_READY"] = "true"
    with tempfile.TemporaryDirectory() as tmp:
        task_id, tdir = _make_task(Path(tmp))
        _add_excel(tdir, "4.0", "a.xlsx")
        _add_excel(tdir, "5.1", "b.xlsx")
        add_source(tdir, {"version": "4.0", "mode": "current_page", "url": "u1", "status": "completed"})
        add_source(tdir, {"version": "5.1", "mode": "current_page", "url": "u2", "status": "failed", "errors": ["404"]})
        fake = FakeClient()
        calls = []
        old_start = bot_service._start_worker
        bot_service._start_worker = lambda *args, **kwargs: calls.append(args) or SimpleNamespace(pid=1)
        try:
            started = bot_service._start_ready_task(task_id, tdir, fake, "user", manual_ignore_failed=True)
        finally:
            bot_service._start_worker = old_start
        assert started
        assert len(calls) == 1
        assert any("忽略" in msg for msg in fake.messages)


def test_retry_failed_sources_resets_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_id, tdir = _make_task(Path(tmp))
        add_source(tdir, {"version": "4.0", "mode": "current_page", "url": "u1", "status": "failed", "errors": ["timeout"]})
        update_source(tdir, "u1", status="pending", errors=[], downloaded_count=0)
        data = bot_service.load_confluence_sources(tdir, task_id)
        assert data["sources"][0]["status"] == "pending"
        assert data["sources"][0]["errors"] == []


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            func()
            print(f"PASS {name}")
