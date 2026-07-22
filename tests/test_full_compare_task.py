from __future__ import annotations

import threading
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_service
from core.bot_task_store import set_active_task_id
from core.confluence_client import ConfluenceClient, attachment_local_filename
from core.confluence_page_selection import classify_page, parse_page_version, select_latest_version_pages
from core.full_compare_task import FullCompareBusyError, create_full_matrix_compare_task
from core.confluence_task_store import update_source
from core.review_store import load_task_meta, update_task_meta


URL40 = "https://yfconfluence.mychery.com/spaces/X/pages/104890412/R1"
URL51 = "https://yfconfluence.mychery.com/spaces/X/pages/132350051/R2"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FULL_COMPARE_40_PARENT_URL", URL40)
    monkeypatch.setenv("FULL_COMPARE_51_PARENT_URL", URL51)
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://yfconfluence.mychery.com")
    monkeypatch.setenv("CONFLUENCE_PAT", "test-pat")
    monkeypatch.setenv("FULL_COMPARE_MAX_CONCURRENT_TASKS", "1")


def _tree() -> list[dict]:
    nodes = [{"page_id": "root", "title": "矩阵", "parent_id": "", "ancestor_ids": []}]
    versions = {
        "EMS_REEV": ["V4.90", "V4.91_"],
        "FLZCU（VCU）": ["V4.80", "V4.81_", "V4.82"],
        "PDCS": ["V4.30", "V4.31", "V4.32"],
    }
    for index, (module, values) in enumerate(versions.items()):
        mid = f"m{index}"
        nodes.append({"page_id": mid, "title": module, "parent_id": "root", "ancestor_ids": ["root"]})
        for offset, title in enumerate(values):
            nodes.append({"page_id": f"{mid}v{offset}", "title": title, "parent_id": mid, "ancestor_ids": ["root", mid]})
    nodes.extend([
        {"page_id": "evcc", "title": "EVCC-V4.7", "parent_id": "root", "ancestor_ids": ["root"]},
        {"page_id": "isg", "title": "ISG-V3.7", "parent_id": "root", "ancestor_ids": ["root"]},
        {"page_id": "hcu", "title": "HCU", "parent_id": "root", "ancestor_ids": ["root"]},
        {"page_id": "bcm", "title": "BCM", "parent_id": "root", "ancestor_ids": ["root"]},
    ])
    return nodes


def test_version_parser_and_classification() -> None:
    assert parse_page_version("V4.10").version_tuple > parse_page_version("V4.9").version_tuple
    assert parse_page_version("v4.82（当前）").normalized_version == "V4.82"
    assert parse_page_version("V4_91_").version_tuple == (4, 91)
    assert classify_page("EVCC-V4.7") == "direct_versioned_module"
    assert classify_page("ISG-V3.7") == "direct_versioned_module"


def test_latest_versions_are_selected_per_module() -> None:
    result = select_latest_version_pages(_tree())
    by_title = {item["module_title"]: item for item in result["selections"]}
    assert by_title["EMS_REEV"]["selected_page_title"] == "V4.91_"
    assert by_title["FLZCU（VCU）"]["selected_page_title"] == "V4.82"
    assert by_title["PDCS"]["selected_page_title"] == "V4.32"
    assert by_title["EVCC-V4.7"]["selected_page_id"] == "evcc"
    assert by_title["HCU"]["selection_reason"] == "direct_unversioned_leaf_module"
    assert by_title["BCM"]["selected_page_id"] == "bcm"
    assert len(by_title["EMS_REEV"]["skipped_pages"]) == 1


def test_strict_equal_version_is_ambiguous() -> None:
    tree = _tree() + [{"page_id": "duplicate", "title": "V4.91", "parent_id": "m0", "ancestor_ids": ["root", "m0"]}]
    result = select_latest_version_pages(tree, strict=True)
    ems = next(item for item in result["selections"] if item["module_key"] == "m0")
    assert result["strict_blocked"] is True
    assert ems["selected_page_id"] == ""
    assert ems["warnings"]


def test_latest_discovery_only_queries_selected_pages_and_filters_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object.__new__(ConfluenceClient)
    client.max_file_size = 1024 * 1024
    monkeypatch.setenv("CONFLUENCE_ATTACHMENT_EXCLUDE_KEYWORDS", "历史,backup")
    client.build_page_tree = lambda page_id, source_url: {"nodes": _tree(), "errors": [], "root_page_id": page_id}
    queried = []

    def list_attachments(page_id: str):
        queried.append(page_id)
        return [
            {"id": f"{page_id}-new", "title": f"{page_id}.xlsx", "version": {"number": 2}, "extensions": {"fileSize": 10}, "_links": {"download": "/new"}},
            {"id": f"{page_id}-tmp", "title": "~$temp.xlsx", "extensions": {"fileSize": 10}},
            {"id": f"{page_id}-old", "title": "矩阵_backup.xlsx", "extensions": {"fileSize": 10}},
            {"id": f"{page_id}-txt", "title": "notes.txt", "extensions": {"fileSize": 10}},
        ]

    client.list_attachments = list_attachments
    attachments, selection = client.discover_latest_excel_attachments("root", "https://example.test/root")
    assert "m0v0" not in queried  # EMS_REEV historical V4.90
    assert "m0v1" in queried
    assert "hcu" in queried and "bcm" in queried
    assert all(item["file_name"].endswith(".xlsx") and not item["file_name"].startswith("~$") for item in attachments)
    assert len(selection["excluded_attachments"]) == len(queried) * 3


def test_same_name_attachment_keeps_highest_confluence_version(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object.__new__(ConfluenceClient)
    client.max_file_size = 1024 * 1024
    tree = [
        {"page_id": "root", "title": "root", "parent_id": "", "ancestor_ids": []},
        {"page_id": "module", "title": "EMS", "parent_id": "root", "ancestor_ids": ["root"]},
        {"page_id": "latest", "title": "V4.2", "parent_id": "module", "ancestor_ids": ["root", "module"]},
    ]
    client.build_page_tree = lambda page_id, source_url: {"nodes": tree, "errors": []}
    client.list_attachments = lambda page_id: [
        {"id": "old", "title": "matrix.xlsx", "version": {"number": 1}, "extensions": {"fileSize": 10}, "_links": {"download": "/old"}},
        {"id": "new", "title": "matrix.xlsx", "version": {"number": 3}, "extensions": {"fileSize": 10}, "_links": {"download": "/new"}},
    ]
    attachments, selection = client.discover_latest_excel_attachments("root")
    assert [item["attachment_id"] for item in attachments] == ["new"]
    assert any(item.get("reason") == "同名附件的旧版本" for item in selection["excluded_attachments"])


def test_same_name_on_different_modules_is_not_dropped_before_sha_deduplication() -> None:
    client = object.__new__(ConfluenceClient)
    client.max_file_size = 1024 * 1024
    tree = [
        {"page_id": "root", "title": "root", "parent_id": "", "ancestor_ids": []},
        {"page_id": "hcu", "title": "HCU", "parent_id": "root", "ancestor_ids": ["root"]},
        {"page_id": "pdcs", "title": "PDCS", "parent_id": "root", "ancestor_ids": ["root"]},
    ]
    client.build_page_tree = lambda page_id, source_url: {"nodes": tree, "errors": []}
    client.list_attachments = lambda page_id: [
        {"id": f"{page_id}-file", "title": "shared.xlsx", "version": {"number": 1}, "extensions": {"fileSize": 10}, "_links": {"download": f"/{page_id}"}},
    ]
    attachments, _ = client.discover_latest_excel_attachments("root")
    assert {(item["page_id"], item["attachment_id"]) for item in attachments} == {("hcu", "hcu-file"), ("pdcs", "pdcs-file")}


def test_content_duplicates_are_audited_but_preserved() -> None:
    records = [
        {"page_id": "pageA", "attachment_id": "att001", "attachment_version": 1, "original_filename": "HCU矩阵.xlsx", "sha256": "same"},
        {"page_id": "pageB", "attachment_id": "att002", "attachment_version": 1, "original_filename": "PDCS矩阵.xlsx", "sha256": "same"},
    ]
    bot_service._annotate_content_duplicates(records)
    assert all(item["content_duplicate"] is True for item in records)
    assert records[0]["duplicate_with"] == ["pageB:att002:v1:m:PDCS矩阵.xlsx"]
    assert records[1]["preserved_reason"] == "different_business_source"


def test_attachment_local_filename_is_readable_stable_and_identity_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUENCE_LOCAL_FILENAME_MAX_CHARS", "140")
    base = {
        "module_title": "HCU/动力域",
        "selected_version": "V4.2",
        "file_name": "Communication:Matrix.xlsx",
        "page_id": "10001",
        "attachment_id": "att001",
        "attachment_version": 3,
    }
    first = attachment_local_filename(base)
    assert first == attachment_local_filename(dict(base))
    assert "__p10001__aatt001v3__s" in first and first.endswith(".xlsx")
    assert "/" not in first and ":" not in first
    assert len(first) <= 140
    assert attachment_local_filename({**base, "page_id": "10002"}) != first
    assert attachment_local_filename({**base, "attachment_id": "att002"}) != first
    assert attachment_local_filename({**base, "attachment_version": 4}) != first
    assert attachment_local_filename({**base, "module_title": "PDCS"}) != first
    assert attachment_local_filename({**base, "file_name": "Other.xlsx"}) != first


def test_retry_download_reuses_same_stable_local_filename(tmp_path: Path) -> None:
    client = object.__new__(ConfluenceClient)
    client.base_url = "https://confluence.example"
    client.max_file_size = 1024
    payloads = [b"first-complete-file", b"second-complete-file"]

    class Response:
        headers = {}

        def __init__(self, payload: bytes):
            self.payload = payload

        def iter_content(self, chunk_size: int):
            yield self.payload

    client._request = lambda *args, **kwargs: Response(payloads.pop(0))
    attachment = {
        "module_title": "HCU",
        "selected_version": "V4.2",
        "file_name": "Matrix.xlsx",
        "page_id": "10001",
        "attachment_id": "att001",
        "attachment_version": 3,
        "download_link": "/download/att001",
    }
    first = client.download_attachment(attachment, tmp_path)
    second = client.download_attachment(attachment, tmp_path)
    assert first == second
    assert second.read_bytes() == b"second-complete-file"
    assert list(tmp_path.glob("*.xlsx")) == [second]
    assert not list(tmp_path.glob("*.download"))


def test_unified_entry_registers_two_sources_and_is_persistently_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    first = create_full_matrix_compare_task("email_auto", "mail-1", "sender@example.com", "user", "ou_notify", root=tmp_path)
    update_task_meta(first.task_dir, status="awaiting_review")
    second = create_full_matrix_compare_task("email_auto", "mail-1", "sender@example.com", "user", "ou_notify", root=tmp_path)
    assert second.duplicate is True
    assert second.task_id == first.task_id
    meta = load_task_meta(first.task_dir)
    assert meta["source"] == "auto_full_compare"
    assert meta["trigger_source"] == "email_auto"
    assert len(meta["registered_sources"]) == 2
    assert (first.task_dir / "bot" / "confluence_sources.json").exists()


def test_concurrent_same_trigger_creates_one_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(create_full_matrix_compare_task("feishu_command", "om_same", "ou_a", "user", "ou_a", root=tmp_path))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({item.task_id for item in results}) == 1
    assert len(list(tmp_path.glob("*/task_meta.json"))) == 1


def test_running_full_compare_blocks_another_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    first = create_full_matrix_compare_task("feishu_command", "om_1", "ou_a", "user", "ou_a", root=tmp_path)
    with pytest.raises(FullCompareBusyError) as exc:
        create_full_matrix_compare_task("feishu_command", "om_2", "ou_b", "user", "ou_b", root=tmp_path)
    assert exc.value.task_id == first.task_id


def test_automatic_full_compare_starts_even_when_manual_autostart_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_START_WHEN_BOTH_READY", "false")
    result = create_full_matrix_compare_task("feishu_command", "om_auto", "ou_a", "user", "ou_a", root=tmp_path)
    for source in result.sources:
        update_source(result.task_dir, source["url"], status="completed", selection_complete=True)
    for version in ("4.0", "5.1"):
        (result.task_dir / "input" / version / "matrix.xlsx").write_bytes(b"excel")
    starts = []
    monkeypatch.setattr(bot_service, "sync_task_progress_card", lambda *args, **kwargs: True)
    monkeypatch.setattr(bot_service, "_start_ready_task", lambda *args, **kwargs: starts.append(args[0]) or True)
    bot_service._maybe_auto_start(result.task_id, result.task_dir, _ReplyClient(), "ou_a")
    assert starts == [result.task_id]


def test_restart_redownloads_completed_tasks_created_with_old_selection_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    result = create_full_matrix_compare_task("feishu_command", "om_old", "ou_a", "user", "ou_a", root=tmp_path)
    for source in result.sources:
        update_source(result.task_dir, source["url"], status="completed", selection_complete=True)
    update_task_meta(result.task_dir, status="downloading")
    for version in ("4.0", "5.1"):
        (result.task_dir / "input" / version / "old-selection.xlsx").write_bytes(b"old")
    resumed = []

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            resumed.append(args[2])

        def start(self):
            return None

    monkeypatch.setattr(bot_service.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(bot_service, "sync_task_progress_card", lambda *args, **kwargs: True)
    monkeypatch.setattr(bot_service, "scan_and_notify", lambda *args, **kwargs: None)
    bot_service.recover_on_start(_ReplyClient())
    assert {item["version"] for item in resumed} == {"4.0", "5.1"}
    assert not list((result.task_dir / "input").glob("*/*.xls*"))


def test_cancel_command_marks_task_cancelled_and_terminates_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    result = create_full_matrix_compare_task("feishu_command", "om_running", "ou_a", "user", "ou_a", root=tmp_path)
    set_active_task_id("ou_a", result.task_id, "oc_chat", tmp_path)
    update_task_meta(result.task_dir, status="running", worker_pid=0)

    class FakeProcess:
        pid = 0

        def __init__(self):
            self.terminated = 0

        def terminate(self):
            self.terminated += 1

    process = FakeProcess()
    bot_service._WORKER_PROCESSES[result.task_id] = process
    monkeypatch.setattr(bot_service, "sync_task_progress_card", lambda *args, **kwargs: True)
    client = _ReplyClient()
    bot_service.handle_event(
        {"message_id": "om_cancel", "sender_id": "ou_a", "chat_id": "oc_chat", "content": '{"text":"取消自动全量任务"}'},
        client,
    )
    meta = load_task_meta(result.task_dir)
    assert meta["status"] == "cancelled"
    assert meta["current_stage"] == "已取消"
    assert process.terminated == 1
    assert "已取消自动全量任务" in client.replies[-1][1]


class _ReplyClient:
    def __init__(self) -> None:
        self.replies = []

    def reply_text(self, message_id: str, text: str) -> None:
        self.replies.append((message_id, text))


def test_full_compare_command_only_requires_feature_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ReplyClient()
    event = {"message_id": "om_1", "sender_id": "ou_a", "content": '{"text":"创建自动全量任务"}'}
    monkeypatch.setenv("FULL_COMPARE_COMMAND_ENABLED", "false")
    bot_service.handle_event(event, client)
    assert "尚未启用" in client.replies[-1][1]


def test_standard_nested_feishu_event_extracts_open_id_and_command(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_nested"}},
            "message": {
                "message_id": "om_nested",
                "chat_id": "oc_nested",
                "message_type": "text",
                "content": '{"text":"创建自动全量任务"}',
            },
        }
    }
    assert bot_service._sender_id(event) == "ou_nested"
    assert bot_service._message_id(event) == "om_nested"
    assert bot_service._chat_id(event) == "oc_nested"
    assert bot_service._extract_text(event) == "创建自动全量任务"


def test_exact_command_creates_and_schedules_without_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("TASK_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("FULL_COMPARE_COMMAND_ENABLED", "true")
    monkeypatch.setattr(bot_service, "sync_task_progress_card", lambda *args, **kwargs: True)
    started = []

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            started.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(bot_service.threading, "Thread", ImmediateThread)
    client = _ReplyClient()
    event = {"message_id": "om_create", "sender_id": "ou_a", "chat_id": "oc_chat", "content": '{"text":"创建自动全量任务"}'}
    bot_service.handle_event(event, client)
    assert "已创建自动全量信号对比任务" in client.replies[-1][1]
    assert len(started) == 2
    assert all(item[0] is bot_service._download_confluence_source for item in started)
