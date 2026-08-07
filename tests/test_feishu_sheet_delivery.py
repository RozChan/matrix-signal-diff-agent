from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from openpyxl import Workbook

from core.ai_review import SOURCE_SHEETS
from core.feishu_file_delivery import delivery_file_names, register_final_result_files
from core.feishu_sheet_delivery import deliver_task_result_sheets
from core.final_export import FINAL_REVIEW_FILENAME
from core.pipeline import OUTPUT_FILENAMES
from core.review_store import create_task_meta, load_task_meta, update_task_meta


class FakeCustomClient:
    def __init__(self) -> None:
        self.cards: list[tuple[str, str, dict]] = []

    def send_card(self, title: str, markdown: str, **kwargs) -> None:
        self.cards.append((title, markdown, kwargs))


def _workbook(path: Path, *, final: bool = False) -> None:
    workbook = Workbook()
    if final:
        workbook.active.title = SOURCE_SHEETS[0]
        workbook.create_sheet(SOURCE_SHEETS[1])
        for sheet_name in SOURCE_SHEETS:
            workbook[sheet_name].append(["4.0信号名", "5.1信号名", "差异点list", "判断结果", "判断来源"])
            workbook[sheet_name].append(["A", "A", "差异", "不同", "人工"])
    else:
        workbook.active.append(["signal"])
        workbook.active.append(["A"])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()


def _task(tmp_path: Path, monkeypatch) -> Path:
    tdir = tmp_path / "20260804_120000_sheet"
    create_task_meta(tdir, tdir.name, status="final_exported")
    output = tdir / "output"
    _workbook(output / OUTPUT_FILENAMES["full_40"])
    _workbook(output / OUTPUT_FILENAMES["full_51"])
    final = output / FINAL_REVIEW_FILENAME
    _workbook(final, final=True)
    register_final_result_files(tdir, final)
    update_task_meta(
        tdir,
        status="final_exported",
        notify_type="feishu_custom_bot",
        review_completed_at="2026-08-04T05:41:03+00:00",
        full_compare_40_parent_url="https://example/pages/通信矩阵-26R1",
        full_compare_51_parent_url="https://example/pages/通信矩阵-26R2",
    )
    cli = tmp_path / "lark-cli.exe"
    cli.write_bytes(b"cli")
    monkeypatch.setenv("LARK_CLI_PATH", str(cli))
    monkeypatch.setenv("FEISHU_RESULT_FOLDER_TOKEN", "folder-token")
    monkeypatch.setenv("FEISHU_RESULT_SHEET_DELIVERY_ENABLED", "true")
    return tdir


def _success_runner(calls: list[list[str]], run_options: list[dict] | None = None):
    def run(command, **kwargs):
        calls.append(command)
        if run_options is not None:
            run_options.append(kwargs)
        if "+import" in command:
            index = sum("+import" in item for item in calls)
            payload = {
                "ok": True,
                "identity": "user",
                "data": {
                    "ready": True,
                    "failed": False,
                    "ticket": f"ticket-{index}",
                    "token": f"sheet-token-{index}",
                    "url": f"https://example.feishu.cn/sheets/sheet-token-{index}",
                },
            }
        else:
            payload = {"ok": True, "identity": "user", "data": {"permission_public": {"link_share_entity": "tenant_editable"}}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    return run


def test_three_workbooks_become_editable_cloud_sheets_and_one_card(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    run_options: list[dict] = []
    monkeypatch.setattr(subprocess, "run", _success_runner(calls, run_options))
    client = FakeCustomClient()

    result = deliver_task_result_sheets(tdir, custom_client=client)

    assert result["success"] is True
    imports = [call for call in calls if "+import" in call]
    permissions = [call for call in calls if "permission.public" in call]
    assert len(imports) == 3
    assert len(permissions) == 3
    import_indexes = [index for index, call in enumerate(calls) if "+import" in call]
    expected_prefix = ".\\" if os.name == "nt" else "./"
    assert all(call[call.index("--file") + 1].startswith(expected_prefix) for call in imports)
    assert all(not Path(call[call.index("--file") + 1]).is_absolute() for call in imports)
    assert all(
        Path(run_options[index]["cwd"]) == (tdir / "bot" / "feishu_result_files").resolve()
        for index in import_indexes
    )
    assert all(call[call.index("--folder-token") + 1] == "folder-token" for call in imports)
    assert [call[call.index("--name") + 1] for call in imports] == [
        Path(name).stem for name in delivery_file_names(load_task_meta(tdir)).values()
    ]
    assert all("--yes" in call and call[call.index("--as") + 1] == "user" for call in permissions)
    assert all(json.loads(call[call.index("--data") + 1]) == {"link_share_entity": "tenant_editable"} for call in permissions)
    assert len(client.cards) == 1
    assert "buttons" not in client.cards[0][2]
    markdown_lines = client.cards[0][1].splitlines()
    file_names = delivery_file_names(load_task_meta(tdir))
    assert markdown_lines[-3:] == [
        f"[{Path(file_names['compare_final']).stem}](https://example.feishu.cn/sheets/sheet-token-3)",
        f"[{Path(file_names['full_40']).stem}](https://example.feishu.cn/sheets/sheet-token-1)",
        f"[{Path(file_names['full_51']).stem}](https://example.feishu.cn/sheets/sheet-token-2)",
    ]
    assert all(line.startswith("[") and "](" in line for line in markdown_lines[-3:])
    assert "最终结果状态：已生成" in client.cards[0][1]
    assert "飞书云表格数量" not in client.cards[0][1]
    assert "Chery组织内获得链接的人可编辑" not in client.cards[0][1]
    meta = load_task_meta(tdir)
    assert meta["status"] == "delivered"
    assert meta["result_delivery_status"] == "delivered"
    assert all(sheet["permission_status"] == "tenant_editable" for sheet in meta["feishu_sheet_delivery"]["spreadsheets"].values())

    repeated = deliver_task_result_sheets(tdir, custom_client=client)
    assert repeated["success"] is True
    assert len(calls) == 6
    assert len(client.cards) == 1


def test_permission_failure_reuses_created_sheet_and_resumes_remaining(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    permission_count = 0

    def fail_second_permission(command, **_kwargs):
        nonlocal permission_count
        calls.append(command)
        if "+import" in command:
            index = sum("+import" in item for item in calls)
            payload = {"ok": True, "data": {"ready": True, "failed": False, "ticket": f"ticket-{index}", "token": f"token-{index}", "url": f"https://feishu/sheets/token-{index}"}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        permission_count += 1
        if permission_count == 2:
            return subprocess.CompletedProcess(command, 3, stdout="", stderr="permission denied")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"ok": True, "data": {}}), stderr="")

    monkeypatch.setattr(subprocess, "run", fail_second_permission)
    client = FakeCustomClient()
    failed = deliver_task_result_sheets(tdir, custom_client=client)
    assert failed["success"] is False
    assert failed["spreadsheets"]["full_40"]["status"] == "success"
    assert failed["spreadsheets"]["full_51"]["token"] == "token-2"
    assert failed["spreadsheets"]["full_51"]["status"] == "failed"
    assert failed["spreadsheets"]["compare_final"]["status"] == "pending"

    retry_calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _success_runner(retry_calls))
    recovered = deliver_task_result_sheets(tdir, custom_client=client, force_notification=True)
    assert recovered["success"] is True
    assert len([call for call in retry_calls if "+import" in call]) == 1
    assert len([call for call in retry_calls if "permission.public" in call]) == 2
    assert load_task_meta(tdir)["feishu_sheet_delivery"]["spreadsheets"]["full_51"]["token"] == "token-2"


def test_import_ticket_is_persisted_and_polled(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def async_runner(command, **_kwargs):
        calls.append(command)
        if "+import" in command:
            index = sum("+import" in item for item in calls)
            payload = {"ok": True, "data": {"ready": False, "failed": False, "ticket": f"ticket-{index}"}}
        elif "+task_result" in command:
            ticket = command[command.index("--ticket") + 1]
            payload = {"ok": True, "data": {"ready": True, "failed": False, "ticket": ticket, "token": f"token-{ticket}", "url": f"https://feishu/sheets/{ticket}"}}
        else:
            payload = {"ok": True, "data": {}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", async_runner)
    result = deliver_task_result_sheets(tdir, custom_client=FakeCustomClient())
    assert result["success"] is True
    assert len([call for call in calls if "+task_result" in call]) == 3
    assert all(value["ticket"].startswith("ticket-") for value in result["spreadsheets"].values())


def test_missing_cli_configuration_fails_without_import(tmp_path: Path, monkeypatch) -> None:
    tdir = _task(tmp_path, monkeypatch)
    monkeypatch.delenv("LARK_CLI_PATH", raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = deliver_task_result_sheets(tdir, custom_client=FakeCustomClient())
    assert result["success"] is False
    assert result["failed_stage"] == "configuration"
    assert "LARK_CLI_PATH" in result["last_error"]
    assert calls == []
