"""Idempotently import the three final workbooks as Feishu cloud spreadsheets."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .feishu_doc_service import extract_last_json_object
from .feishu_file_delivery import (
    DELIVERY_ORDER,
    _registered_files,
    _stage_files,
    delivery_file_names,
)
from .review_store import load_task_meta, update_task_meta, utc_now_iso


class FeishuSheetDeliveryError(RuntimeError):
    def __init__(self, stage: str, message: str, *, stdout: str = "", stderr: str = "") -> None:
        self.stage = stage
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


def _enabled() -> bool:
    return os.getenv("FEISHU_RESULT_SHEET_DELIVERY_ENABLED", "true").strip().lower() == "true"


def _redact(text: str) -> str:
    cleaned = str(text or "")
    for name in ("FEISHU_CUSTOM_BOT_WEBHOOK", "FEISHU_CUSTOM_BOT_SECRET", "FEISHU_RESULT_FOLDER_TOKEN"):
        value = os.getenv(name, "").strip()
        if value:
            cleaned = cleaned.replace(value, f"<{name.lower()}-redacted>")
    return re.sub(
        r"(?i)(tenant_access_token|user_access_token|refresh_token|app_secret)\s*[:=]\s*[^\s,}\"]+",
        r"\1=<redacted>",
        cleaned,
    )


def _environment() -> tuple[str, str]:
    raw_cli_path = os.getenv("LARK_CLI_PATH", "").strip()
    folder_token = os.getenv("FEISHU_RESULT_FOLDER_TOKEN", "").strip()
    if not raw_cli_path:
        raise FeishuSheetDeliveryError("configuration", "LARK_CLI_PATH未配置")
    cli_path = Path(raw_cli_path)
    if not cli_path.is_file():
        raise FeishuSheetDeliveryError("configuration", f"lark-cli不存在：{cli_path}")
    if not folder_token:
        raise FeishuSheetDeliveryError("configuration", "FEISHU_RESULT_FOLDER_TOKEN未配置")
    return str(cli_path), folder_token


def _classify_cli_error(text: str, default: str) -> str:
    lowered = str(text or "").lower()
    if "need_user_authorization" in lowered or "token_missing" in lowered:
        return "user_authorization_missing"
    if "permission" in lowered or "forbidden" in lowered or "scope" in lowered:
        return "permission_denied"
    if any(code in lowered for code in ("232140101", "232140100", "233523001")):
        return "import_conflict"
    return default


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("data")
    return dict(payload) if data.get("ok") is not None and isinstance(payload, dict) else data


def _run_cli(
    args: list[str],
    *,
    timeout: int,
    stage: str,
    cwd: Path | None = None,
) -> dict[str, Any]:
    cli_path, _folder_token = _environment()
    command = [cli_path, *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise FeishuSheetDeliveryError(
            stage,
            f"{stage}执行超时",
            stdout=_redact(exc.stdout or ""),
            stderr=_redact(exc.stderr or ""),
        ) from exc
    except OSError as exc:
        raise FeishuSheetDeliveryError(stage, f"无法启动lark-cli：{exc}") from exc
    stdout, stderr = _redact(result.stdout), _redact(result.stderr)
    if result.returncode != 0:
        detail = stderr or stdout
        raise FeishuSheetDeliveryError(
            _classify_cli_error(detail, stage),
            f"{stage}失败（exit={result.returncode}）：{detail}",
            stdout=stdout,
            stderr=stderr,
        )
    data = extract_last_json_object(result.stdout)
    if data.get("ok") is False or int(data.get("code", 0) or 0) != 0:
        detail = str(data.get("error") or data.get("message") or data.get("msg") or data)
        raise FeishuSheetDeliveryError(
            _classify_cli_error(detail, stage),
            f"{stage}业务失败：{detail}",
            stdout=stdout,
            stderr=stderr,
        )
    return _payload(data)


def _import_result(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("failed") is True:
        raise FeishuSheetDeliveryError("import", f"云表格导入失败：{payload.get('job_error_msg') or payload}")
    token = str(payload.get("token") or "").strip()
    url = str(payload.get("url") or "").strip()
    if payload.get("ready") is True and token and url:
        return {"token": token, "url": url, "ticket": str(payload.get("ticket") or "")}
    return {"token": "", "url": "", "ticket": str(payload.get("ticket") or "")}


def _start_import(path: Path, title: str, folder_token: str) -> dict[str, str]:
    path = Path(path).resolve()
    relative_path = f".\\{path.name}" if os.name == "nt" else f"./{path.name}"
    payload = _run_cli(
        [
            "drive", "+import", "--file", relative_path, "--type", "sheet",
            "--folder-token", folder_token, "--name", title,
            "--as", "user", "--format", "json",
        ],
        timeout=int(os.getenv("FEISHU_SHEET_IMPORT_COMMAND_TIMEOUT_SECONDS", "240")),
        stage="spreadsheet_import",
        cwd=path.parent,
    )
    return _import_result(payload)


def _poll_import(ticket: str) -> dict[str, str]:
    deadline = time.monotonic() + max(30, int(os.getenv("FEISHU_SHEET_IMPORT_POLL_TIMEOUT_SECONDS", "300")))
    interval = max(1, int(os.getenv("FEISHU_SHEET_IMPORT_POLL_INTERVAL_SECONDS", "3")))
    while time.monotonic() < deadline:
        payload = _run_cli(
            [
                "drive", "+task_result", "--scenario", "import", "--ticket", ticket,
                "--as", "user", "--format", "json",
            ],
            timeout=int(os.getenv("FEISHU_SHEET_IMPORT_STATUS_TIMEOUT_SECONDS", "60")),
            stage="spreadsheet_import_status",
        )
        result = _import_result(payload)
        if result["token"] and result["url"]:
            return result
        time.sleep(interval)
    raise FeishuSheetDeliveryError("spreadsheet_import_status", f"云表格导入仍在处理中，可使用ticket继续重试：{ticket}")


def _set_tenant_editable(token: str) -> None:
    _run_cli(
        [
            "drive", "permission.public", "patch",
            "--token", token, "--type", "sheet",
            "--data", json.dumps({"link_share_entity": "tenant_editable"}, ensure_ascii=False),
            "--as", "user", "--yes", "--format", "json",
        ],
        timeout=int(os.getenv("FEISHU_SHEET_PERMISSION_TIMEOUT_SECONDS", "60")),
        stage="spreadsheet_permission",
    )


def _default_delivery(names: dict[str, str]) -> dict[str, Any]:
    return {
        "status": "pending",
        "attempt_count": 0,
        "started_at": "",
        "completed_at": "",
        "updated_at": "",
        "last_error": "",
        "failed_stage": "",
        "permission_mode": "tenant_editable",
        "card_status": "pending",
        "spreadsheets": {
            key: {
                "status": "pending",
                "display_name": names[key],
                "title": Path(names[key]).stem,
                "attempt_count": 0,
                "ticket": "",
                "token": "",
                "url": "",
                "permission_status": "pending",
                "last_error": "",
            }
            for key in DELIVERY_ORDER
        },
    }


def _normalized_delivery(existing: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    delivery = _default_delivery(names)
    delivery.update({key: value for key, value in existing.items() if key != "spreadsheets"})
    old_sheets = dict(existing.get("spreadsheets") or {})
    for key in DELIVERY_ORDER:
        delivery["spreadsheets"][key].update(dict(old_sheets.get(key) or {}))
        delivery["spreadsheets"][key]["display_name"] = names[key]
        delivery["spreadsheets"][key]["title"] = Path(names[key]).stem
    return delivery


def _save(task_dir: Path, delivery: dict[str, Any]) -> None:
    delivery["updated_at"] = utc_now_iso()
    status = str(delivery.get("status") or "failed")
    summary = status if status in {"pending", "creating", "ready", "failed", "delivered"} else "failed"
    update_task_meta(
        task_dir,
        feishu_sheet_delivery=delivery,
        result_delivery_status=summary,
        delivery_error=str(delivery.get("last_error") or ""),
    )


def _claim(task_dir: Path) -> Path:
    claim = Path(task_dir) / "bot" / "feishu_sheet_delivery.lock"
    claim.parent.mkdir(parents=True, exist_ok=True)
    stale_seconds = max(60, int(os.getenv("FEISHU_SHEET_DELIVERY_LOCK_STALE_SECONDS", "900")))
    if claim.exists() and datetime.now().timestamp() - claim.stat().st_mtime > stale_seconds:
        claim.unlink(missing_ok=True)
    try:
        descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise FeishuSheetDeliveryError("running", "飞书云表格正在创建") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} at={utc_now_iso()}")
    return claim


def _notify(task_dir: Path, delivery: dict[str, Any], *, custom_client: Any | None, force: bool) -> bool:
    from .notification_router import notify_result_ready

    notified = notify_result_ready(task_dir, custom_client=custom_client, force=force)
    delivery["card_status"] = "sent" if notified else "failed"
    if not notified:
        delivery["last_error"] = "三个云表格已创建，但结果通知或链接消息发送失败"
    _save(task_dir, delivery)
    return notified


def deliver_task_result_sheets(
    task_dir: str | Path,
    *,
    custom_client: Any | None = None,
    force_notification: bool = False,
) -> dict[str, Any]:
    """Create or resume three cloud sheets, then send one card and three links."""

    tdir = Path(task_dir).resolve()
    if not _enabled():
        return {"success": False, "status": "disabled", "last_error": "飞书云表格交付未启用"}
    try:
        claim = _claim(tdir)
    except FeishuSheetDeliveryError as exc:
        return {"success": False, "status": "running", "failed_stage": exc.stage, "last_error": str(exc)}
    try:
        meta = load_task_meta(tdir)
        try:
            files = _registered_files(tdir, meta)
            names = delivery_file_names(meta)
            delivery = _normalized_delivery(dict(meta.get("feishu_sheet_delivery") or {}), names)
            if delivery.get("status") == "delivered" and all(
                delivery["spreadsheets"][key].get("status") == "success" for key in DELIVERY_ORDER
            ):
                return {"success": True, **delivery}
            _cli_path, folder_token = _environment()
            staged = _stage_files(tdir, files, names)
            delivery.update({
                "status": "creating",
                "attempt_count": int(delivery.get("attempt_count") or 0) + 1,
                "started_at": delivery.get("started_at") or utc_now_iso(),
                "last_error": "",
                "failed_stage": "",
            })
            _save(tdir, delivery)

            for key in DELIVERY_ORDER:
                sheet = delivery["spreadsheets"][key]
                if sheet.get("status") == "success" and sheet.get("url") and sheet.get("permission_status") == "tenant_editable":
                    continue
                sheet.update({
                    "status": "creating",
                    "attempt_count": int(sheet.get("attempt_count") or 0) + 1,
                    "last_error": "",
                })
                _save(tdir, delivery)
                try:
                    if sheet.get("token") and sheet.get("url"):
                        imported = {"token": str(sheet["token"]), "url": str(sheet["url"]), "ticket": str(sheet.get("ticket") or "")}
                    elif sheet.get("ticket"):
                        imported = _poll_import(str(sheet["ticket"]))
                    else:
                        imported = _start_import(staged[key], str(sheet["title"]), folder_token)
                        if imported.get("ticket"):
                            sheet["ticket"] = imported["ticket"]
                            _save(tdir, delivery)
                        if not imported.get("token"):
                            if not imported.get("ticket"):
                                raise FeishuSheetDeliveryError("spreadsheet_import", "导入响应缺少token、URL和ticket")
                            imported = _poll_import(imported["ticket"])
                    sheet.update({
                        "ticket": imported.get("ticket") or sheet.get("ticket") or "",
                        "token": imported["token"],
                        "url": imported["url"],
                        "status": "permission",
                    })
                    _save(tdir, delivery)
                    if sheet.get("permission_status") != "tenant_editable":
                        _set_tenant_editable(str(sheet["token"]))
                    sheet.update({"status": "success", "permission_status": "tenant_editable", "last_error": ""})
                    _save(tdir, delivery)
                except Exception as exc:  # noqa: BLE001 - preserve token/ticket for retry
                    stage = exc.stage if isinstance(exc, FeishuSheetDeliveryError) else "unexpected"
                    sheet.update({"status": "failed", "last_error": str(exc)})
                    delivery.update({"status": "failed", "failed_stage": stage, "last_error": f"{sheet['display_name']}：{exc}"})
                    _save(tdir, delivery)
                    _notify(tdir, delivery, custom_client=custom_client, force=force_notification)
                    return {"success": False, **delivery}

            delivery.update({"status": "ready", "completed_at": utc_now_iso(), "failed_stage": "", "last_error": ""})
            _save(tdir, delivery)
            if not _notify(tdir, delivery, custom_client=custom_client, force=force_notification):
                return {"success": False, **delivery}
            delivery.update({"status": "delivered", "completed_at": utc_now_iso(), "last_error": ""})
            _save(tdir, delivery)
            update_task_meta(tdir, status="delivered", result_delivered_at=utc_now_iso())
            return {"success": True, **delivery}
        except Exception as exc:  # noqa: BLE001 - persist configuration/validation failures
            stage = exc.stage if isinstance(exc, FeishuSheetDeliveryError) else "unexpected"
            meta = load_task_meta(tdir)
            try:
                names = delivery_file_names(meta)
            except Exception:  # noqa: BLE001
                names = {key: key for key in DELIVERY_ORDER}
            delivery = _normalized_delivery(dict(meta.get("feishu_sheet_delivery") or {}), names)
            delivery.update({"status": "failed", "failed_stage": stage, "last_error": str(exc)})
            _save(tdir, delivery)
            _notify(tdir, delivery, custom_client=custom_client, force=force_notification)
            return {"success": False, **delivery}
    finally:
        claim.unlink(missing_ok=True)
