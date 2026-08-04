from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.feishu_doc_service import (
    ATTACHMENT_LABELS,
    FeishuDocumentError,
    extract_last_json_object,
    publish_task_result_document,
    register_final_result_files,
    retry_failed_attachments,
    retry_result_notification,
)
from core.final_export import FINAL_REVIEW_FILENAME, SHEET_RULES
from core.notification_router import notify_result_ready
from core.pipeline import OUTPUT_FILENAMES
from core.review_store import create_task_meta, init_review_state, load_task_meta, update_task_meta


def _workbook(path: Path, sheets: list[str] | None = None) -> None:
    workbook = Workbook()
    if sheets:
        workbook.remove(workbook.active)
        for name in sheets:
            sheet = workbook.create_sheet(name)
            sheet.append(["header"])
            sheet.append(["value"])
    else:
        workbook.active.append(["header"])
        workbook.active.append(["value"])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def _task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tdir = tmp_path / "task1"
    create_task_meta(tdir, "task1")
    update_task_meta(
        tdir,
        status="final_exported",
        notify_type="feishu_custom_bot",
        review_completed_at="2026-07-29T01:02:03+00:00",
        input_40_count=2,
        input_51_count=3,
    )
    output = tdir / "output"
    _workbook(output / OUTPUT_FILENAMES["full_40"])
    _workbook(output / OUTPUT_FILENAMES["full_51"])
    final_path = output / FINAL_REVIEW_FILENAME
    _workbook(final_path, list(SHEET_RULES))
    review_dir = tdir / "review"
    items = [{"item_id": "a", "field_diffs": [], "signal_ai_judgement": "无法判断"}]
    (review_dir / "review_items.json").parent.mkdir(parents=True, exist_ok=True)
    (review_dir / "review_items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    init_review_state(review_dir, "task1", items)
    register_final_result_files(tdir, final_path)
    cli = tmp_path / "lark-cli.exe"
    cli.write_bytes(b"cli")
    monkeypatch.setenv("LARK_CLI_PATH", str(cli))
    monkeypatch.setenv("FEISHU_RESULT_FOLDER_TOKEN", "folder-token")
    monkeypatch.setenv("FEISHU_DOC_DELIVERY_ENABLED", "true")
    return tdir


def _success_run(calls: list[tuple[list[str], str | None]]):
    def run(command, **kwargs):
        calls.append((command, kwargs.get("cwd")))
        if "+create" in command:
            payload = {"data": {"document": {"document_id": "doc-1", "url": "https://feishu/doc-1"}}}
        else:
            index = sum("+media-insert" in item[0] for item in calls)
            payload = {"data": {"document_id": "doc-1", "block_id": f"block-{index}", "file_token": f"file-{index}", "type": "file"}}
        return subprocess.CompletedProcess(command, 0, stdout=f"progress {{not json}}\n{json.dumps(payload, ensure_ascii=False)}\n", stderr="")

    return run


def test_extract_last_json_from_mixed_cli_output() -> None:
    output = 'Creating {ordinary braces}\n{"first": 1}\n上传中文附件\n{"data":{"document":{"document_id":"doc"}}}'
    assert extract_last_json_object(output)["data"]["document"]["document_id"] == "doc"
    with pytest.raises(FeishuDocumentError, match="无法从"):
        extract_last_json_object("only process logs {broken}")


def test_publish_uses_exact_three_registered_files_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(subprocess, "run", _success_run(calls))

    result = publish_task_result_document(tdir, notify=False)
    assert result["success"] and result["status"] == "ready"
    assert len(calls) == 4
    assert calls[0][0][1:3] == ["docs", "+create"]
    assert "--parent-token" in calls[0][0] and "folder-token" in calls[0][0]
    uploaded = [Path(call[0][call[0].index("--file") + 1]).name for call in calls[1:]]
    assert uploaded == [ATTACHMENT_LABELS[key] for key in ("full_40", "full_51", "compare_final")]
    assert all(call[0][call[0].index("--as") + 1] == "user" for call in calls)
    assert all(Path(str(call[1])).name == "feishu_delivery_attachments" for call in calls[1:])
    assert all((tdir / "output" / name).is_file() for name in (OUTPUT_FILENAMES["full_40"], OUTPUT_FILENAMES["full_51"], FINAL_REVIEW_FILENAME))
    create_command = calls[0][0]
    assert create_command[create_command.index("--title") + 1] == "20260729_090203_信号矩阵全量对比最终结果"
    content = create_command[create_command.index("--content") + 1]
    assert content == """# EEA4.0与EEA5.1信号差异识别结果

## 结果附件
EEA4.0全量信号矩阵清单.xlsx
EEA5.1全量信号矩阵清单.xlsx
人工审核后最终差异结果.xlsx
"""
    saved = load_task_meta(tdir)
    assert saved["result_delivery_status"] == "ready"
    assert all(
        saved["feishu_delivery"]["attachments"][key]["display_name"] == ATTACHMENT_LABELS[key]
        for key in ATTACHMENT_LABELS
    )

    assert publish_task_result_document(tdir, notify=False)["success"]
    assert len(calls) == 4


def test_missing_or_wrong_final_file_stops_before_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    update_task_meta(tdir, final_result_files={**load_task_meta(tdir)["final_result_files"], "compare_final": f"output/{OUTPUT_FILENAMES['compare']}"})
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = publish_task_result_document(tdir, notify=False)
    assert not result["success"] and result["error_type"] == "wrong_compare_result_file"
    assert calls == []


def test_empty_file_is_rejected_before_document_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    (tdir / "output" / OUTPUT_FILENAMES["full_51"]).write_bytes(b"")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = publish_task_result_document(tdir, notify=False)
    assert not result["success"] and result["error_type"] == "invalid_output_file"
    assert calls == []


def test_second_attachment_failure_and_retry_only_remaining_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def fail_second(command, **kwargs):
        calls.append(command)
        if "+create" in command:
            payload = {"data": {"document": {"document_id": "doc-1", "url": "https://feishu/doc-1"}}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        if len([item for item in calls if "+media-insert" in item]) == 2:
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="upload denied")
        payload = {"data": {"block_id": "block-1", "file_token": "file-1"}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fail_second)
    result = publish_task_result_document(tdir, notify=False)
    assert not result["success"] and result["status"] == "partial_failed"
    assert result["attachments"]["full_40"]["status"] == "success"
    assert result["attachments"]["full_51"]["status"] == "failed"
    assert load_task_meta(tdir)["result_delivery_status"] == "failed"

    retry_calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(subprocess, "run", _success_run(retry_calls))
    retried = retry_failed_attachments(tdir)
    assert retried["success"]
    assert len(retry_calls) == 2
    assert all("+media-insert" in call[0] for call in retry_calls)
    assert OUTPUT_FILENAMES["full_40"] not in " ".join(retry_calls[0][0])


def test_cli_missing_create_failure_and_timeout_are_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    monkeypatch.setenv("LARK_CLI_PATH", str(tmp_path / "missing.exe"))
    assert publish_task_result_document(tdir, notify=False)["error_type"] == "lark_cli_not_found"

    monkeypatch.setenv("LARK_CLI_PATH", str(tmp_path / "lark-cli.exe"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 3, stdout="", stderr="permission denied folder-token"),
    )
    assert publish_task_result_document(tdir, notify=False)["error_type"] == "folder_permission_denied"
    serialized_meta = json.dumps(load_task_meta(tdir), ensure_ascii=False)
    assert "folder-token" not in serialized_meta

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    assert publish_task_result_document(tdir, notify=False)["error_type"] == "command_timeout"


def test_env_example_does_not_publish_the_company_folder_token() -> None:
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "FEISHU_RESULT_FOLDER_TOKEN=\n" in env_example.replace("\r\n", "\n")
    assert "BsY1fW5ojlVpYddB8hdchZETn2b" not in env_example


def test_process_claim_prevents_concurrent_document_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls: list[tuple[list[str], str | None]] = []
    normal = _success_run(calls)

    def blocking(command, **kwargs):
        if "+create" in command:
            entered.set()
            release.wait(2)
        return normal(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", blocking)
    first: list[dict] = []
    thread = threading.Thread(target=lambda: first.append(publish_task_result_document(tdir, notify=False)))
    thread.start()
    assert entered.wait(1)
    second = publish_task_result_document(tdir, notify=False)
    release.set()
    thread.join()
    assert second["error_type"] == "delivery_already_running"
    assert first[0]["success"]


class FakeCustomClient:
    def __init__(self) -> None:
        self.cards = []

    def send_card(self, title, markdown, **kwargs):
        self.cards.append((title, markdown, kwargs))


def test_result_notification_uses_document_as_primary_link_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    update_task_meta(
        tdir,
        feishu_delivery={
            "status": "ready",
            "document_id": "doc-1",
            "document_url": "https://feishu/doc-1",
            "document_title": "title",
            "attachments": {key: {"status": "success"} for key in ATTACHMENT_LABELS},
            "result_notification": {"status": "pending"},
        },
    )
    client = FakeCustomClient()
    assert retry_result_notification(tdir, custom_client=client)
    assert len(client.cards) == 1
    assert "飞书交付状态：成功" in client.cards[0][1]
    assert client.cards[0][2]["button_text"] == "打开飞书结果文档"
    assert client.cards[0][2]["button_url"] == "https://feishu/doc-1"
    assert retry_result_notification(tdir, custom_client=client)
    assert len(client.cards) == 1
    assert load_task_meta(tdir)["status"] == "delivered"


def test_failed_document_delivery_notifies_local_download_without_failing_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    update_task_meta(
        tdir,
        feishu_delivery={
            "status": "partial_failed",
            "document_id": "doc-1",
            "document_url": "https://feishu/doc-1",
            "document_title": "title",
            "last_error": "second attachment denied",
            "attachments": {
                "full_40": {"status": "success"},
                "full_51": {"status": "failed"},
                "compare_final": {"status": "pending"},
            },
        },
        result_delivery_status="failed",
    )
    client = FakeCustomClient()

    assert notify_result_ready(tdir, custom_client=client)

    assert len(client.cards) == 1
    assert "飞书交付状态：失败" in client.cards[0][1]
    assert "second attachment denied" in client.cards[0][1]
    assert client.cards[0][2]["button_text"] == "进入结果下载页"
    assert "result_token=" in client.cards[0][2]["button_url"]
    assert load_task_meta(tdir)["status"] == "final_exported"

    update_task_meta(
        tdir,
        feishu_delivery={
            "status": "ready",
            "document_id": "doc-1",
            "document_url": "https://feishu/doc-1",
            "document_title": "title",
            "attachments": {key: {"status": "success"} for key in ATTACHMENT_LABELS},
            "result_notification": {"status": "pending"},
        },
    )
    assert retry_result_notification(tdir, custom_client=client)
    assert len(client.cards) == 2
    assert client.cards[1][2]["button_text"] == "打开飞书结果文档"
    assert client.cards[1][2]["button_url"] == "https://feishu/doc-1"

