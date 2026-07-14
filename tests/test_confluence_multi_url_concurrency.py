from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import bot_service
from core.bot_task_store import atomic_write_json
from core.confluence_source_parser import parse_confluence_sources
from core.confluence_task_store import add_sources, load_confluence_sources, task_lock, update_source
from core.review_store import create_task_meta

REAL_MESSAGE = """4.0页面：https://yfconfluence.mychery.com/spaces/EEA51/pages/109052023/EMS_ICE-V4.9
https://yfconfluence.mychery.com/spaces/EEA51/pages/109052027/EMS_PHEV+HEV-V2.4?src=contextnavpagetreemode
5.1页面：https://yfconfluence.mychery.com/spaces/EEA51/pages/132350055/CDU-V5.0
https://yfconfluence.mychery.com/spaces/EEA51/pages/132350057/ELSD-V1.3"""


class DummyClient:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.sent: list[str] = []

    def reply_text(self, message_id: str, text: str) -> None:
        self.replies.append(text)

    def send_text(self, user_id: str, text: str) -> None:
        self.sent.append(text)


def make_task(tmp_path: Path, task_id: str = "task1") -> Path:
    tdir = tmp_path / task_id
    (tdir / "input" / "4.0").mkdir(parents=True)
    (tdir / "input" / "5.1").mkdir(parents=True)
    create_task_meta(tdir, task_id, status="created")
    return tdir


def sources() -> list[dict[str, object]]:
    return [
        {"version": "4.0", "mode": "current_page", "url": "https://c/pages/109052023", "status": "pending"},
        {"version": "4.0", "mode": "current_page", "url": "https://c/pages/109052027", "status": "pending"},
        {"version": "5.1", "mode": "current_page", "url": "https://c/pages/132350055", "status": "pending"},
        {"version": "5.1", "mode": "current_page", "url": "https://c/pages/132350057", "status": "pending"},
    ]


def test_real_message_parses_four_sources_and_preserves_url_bits() -> None:
    parsed = parse_confluence_sources(REAL_MESSAGE)
    assert parsed.unresolved_urls == []
    assert [(s.version, s.mode, s.url) for s in parsed.sources] == [
        ("4.0", "current_page", "https://yfconfluence.mychery.com/spaces/EEA51/pages/109052023/EMS_ICE-V4.9"),
        ("4.0", "current_page", "https://yfconfluence.mychery.com/spaces/EEA51/pages/109052027/EMS_PHEV+HEV-V2.4?src=contextnavpagetreemode"),
        ("5.1", "current_page", "https://yfconfluence.mychery.com/spaces/EEA51/pages/132350055/CDU-V5.0"),
        ("5.1", "current_page", "https://yfconfluence.mychery.com/spaces/EEA51/pages/132350057/ELSD-V1.3"),
    ]


def test_unresolved_url_is_not_guessed() -> None:
    parsed = parse_confluence_sources("https://yfconfluence.mychery.com/spaces/EEA51/pages/1/X")
    assert parsed.sources == []
    assert parsed.unresolved_urls == ["https://yfconfluence.mychery.com/spaces/EEA51/pages/1/X"]


def test_add_sources_registers_four_once(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    added = add_sources(tdir, sources(), auto_start=True)
    assert len(added) == 4
    added_again = add_sources(tdir, sources(), auto_start=True)
    assert added_again == []
    data = load_confluence_sources(tdir)
    assert data["sources_registration_complete"] is True
    assert len(data["sources"]) == 4


def test_handle_confluence_message_registers_batch_before_threads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(bot_service, "get_active_task_id", lambda sender: "")
    monkeypatch.setattr(bot_service, "create_upload_session", lambda sender, chat, msg: {"task_id": "task1"})
    tdir = make_task(tmp_path)
    monkeypatch.setattr(bot_service, "task_dir", lambda task_id: tdir)
    started: list[dict[str, object]] = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            assert len(load_confluence_sources(tdir)["sources"]) == 4
            started.append(args[2])
        def start(self):
            pass

    monkeypatch.setattr(bot_service.threading, "Thread", FakeThread)
    client = DummyClient()
    assert bot_service._handle_confluence_message({"message_id": "m", "sender_id": "u", "chat_id": "c"}, client, REAL_MESSAGE)
    assert len(started) == 4
    assert "已识别4个Confluence来源" in client.replies[-1]
    assert "4.0当前页面：2个" in client.replies[-1]
    assert "5.1当前页面：2个" in client.replies[-1]


def test_concurrent_update_source_keeps_all_sources(tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    add_sources(tdir, sources(), auto_start=True)

    def worker(src: dict[str, object]) -> None:
        update_source(tdir, str(src["url"]), status="completed", downloaded_count=1)

    threads = [threading.Thread(target=worker, args=(src,)) for src in sources()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = load_confluence_sources(tdir)
    assert len(data["sources"]) == 4
    assert all(item["status"] == "completed" for item in data["sources"])


def test_atomic_write_json_concurrent_parseable_and_unique_temp(tmp_path: Path) -> None:
    path = tmp_path / "confluence_sources.json"

    def writer(index: int) -> None:
        atomic_write_json(path, {"index": index, "items": list(range(index % 7))})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "index" in data
    assert not (tmp_path / "confluence_sources.json.tmp").exists()


def test_worker_starts_only_after_fourth_completion_and_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    (tdir / "input" / "4.0" / "a.xlsx").write_text("x")
    (tdir / "input" / "5.1" / "b.xlsx").write_text("x")
    add_sources(tdir, sources(), auto_start=True)
    monkeypatch.setenv("BOT_AUTO_START_WHEN_BOTH_READY", "true")
    starts = []
    monkeypatch.setattr(bot_service, "_start_worker", lambda task_id, enable_ai=True: starts.append(task_id))
    client = DummyClient()
    for src in sources()[:3]:
        update_source(tdir, str(src["url"]), status="completed")
        bot_service._maybe_auto_start("task1", tdir, client, "u")
    assert starts == []
    update_source(tdir, str(sources()[3]["url"]), status="completed")

    threads = [threading.Thread(target=bot_service._maybe_auto_start, args=("task1", tdir, client, "u")) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert starts == ["task1"]
    data = load_confluence_sources(tdir)
    assert data["worker_started"] is True
    assert data["worker_starting"] is False


def test_failed_source_blocks_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    (tdir / "input" / "4.0" / "a.xlsx").write_text("x")
    (tdir / "input" / "5.1" / "b.xlsx").write_text("x")
    add_sources(tdir, sources(), auto_start=True)
    for src in sources()[:3]:
        update_source(tdir, str(src["url"]), status="completed")
    update_source(tdir, str(sources()[3]["url"]), status="failed", errors=["boom"])
    starts = []
    monkeypatch.setattr(bot_service, "_start_worker", lambda task_id, enable_ai=True: starts.append(task_id))
    client = DummyClient()
    bot_service._maybe_auto_start("task1", tdir, client, "u")
    assert starts == []
    assert any("存在 Confluence 来源下载失败" in msg for msg in client.sent)


def test_state_write_failure_does_not_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tdir = make_task(tmp_path)
    (tdir / "input" / "4.0" / "a.xlsx").write_text("x")
    (tdir / "input" / "5.1" / "b.xlsx").write_text("x")
    add_sources(tdir, sources(), auto_start=True)
    for src in sources():
        update_source(tdir, str(src["url"]), status="completed")
    starts = []
    monkeypatch.setattr(bot_service, "_start_worker", lambda task_id, enable_ai=True: starts.append(task_id))
    monkeypatch.setattr(bot_service, "set_worker_state", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        bot_service._maybe_auto_start("task1", tdir, DummyClient(), "u")
    assert starts == []


def test_handle_event_catches_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    bot_service._PROCESSED.clear()
    monkeypatch.setattr(bot_service, "_handle_confluence_message", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    client = DummyClient()
    bot_service.handle_event({"message_id": "m-exc", "sender_id": "u", "content": json.dumps({"text": "4.0页面：https://c/p"})}, client)
    assert any("本条消息处理失败" in reply for reply in client.replies)
