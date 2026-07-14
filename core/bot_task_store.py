"""Persistent helpers for Feishu bot task metadata and upload sessions."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from .review_store import load_task_meta, save_task_meta, update_task_meta


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_task_root() -> Path:
    return Path(os.getenv("TASK_ROOT_DIR", "temp")).expanduser().resolve()


def task_dir(task_id: str, root: Path | None = None) -> Path:
    return (root or get_task_root()) / task_id


def bot_dir(task_path: Path) -> Path:
    path = Path(task_path) / "bot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_task_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def new_review_token() -> str:
    return secrets.token_urlsafe(32)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    last_error: PermissionError | None = None
    for attempt in range(5):
        tmp_name = ""
        fd = -1
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
            return
        except PermissionError as exc:
            last_error = exc
            if fd >= 0:
                os.close(fd)
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            if attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))
        except Exception:
            if fd >= 0:
                os.close(fd)
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            raise
    if last_error:
        raise last_error


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def build_review_url(task_id: str, review_token: str, base_url: str | None = None) -> str:
    base = (base_url or os.getenv("REVIEW_BASE_URL", "http://localhost:8501")).rstrip("/")
    return f"{base}/?task_id={task_id}&token={review_token}"


def ensure_feishu_meta(task_path: Path, sender_id: str = "", chat_id: str = "", source_message_id: str = "") -> dict[str, Any]:
    meta = load_task_meta(task_path)
    meta.setdefault("source", "feishu")
    meta.setdefault("input_mode", "")
    meta.setdefault("feishu_sender_id", sender_id)
    meta.setdefault("feishu_chat_id", chat_id)
    meta.setdefault("feishu_source_message_id", source_message_id)
    meta.setdefault("feishu_progress_message_id", "")
    meta.setdefault("review_token", "")
    meta.setdefault("review_url", "")
    meta.setdefault("current_stage", "created")
    meta.setdefault("stage_progress", 0)
    meta.setdefault("current_signal", "")
    meta.setdefault("signal_total", 0)
    meta.setdefault("ai_required_signal_count", 0)
    meta.setdefault("ai_completed_signal_count", 0)
    meta.setdefault("ai_failed_signal_count", 0)
    meta.setdefault("notification_status", "pending")
    meta.setdefault("result_delivery_status", "pending")
    meta.setdefault("delivery_error", "")
    meta.setdefault("confluence_source_count", 0)
    meta.setdefault("confluence_page_total", 0)
    meta.setdefault("confluence_page_scanned", 0)
    meta.setdefault("confluence_attachment_total", 0)
    meta.setdefault("confluence_downloaded_count", 0)
    save_task_meta(task_path, meta)
    return meta


def set_review_link(task_path: Path) -> dict[str, Any]:
    meta = load_task_meta(task_path)
    token = meta.get("review_token") or new_review_token()
    url = build_review_url(meta.get("task_id", task_path.name), token)
    return update_task_meta(task_path, review_token=token, review_url=url)


def sessions_path(root: Path | None = None) -> Path:
    return (root or get_task_root()) / "bot_sessions.json"


def load_sessions(root: Path | None = None) -> dict[str, Any]:
    return read_json(sessions_path(root), {})


def save_sessions(sessions: dict[str, Any], root: Path | None = None) -> None:
    atomic_write_json(sessions_path(root), sessions)


def create_upload_session(sender_id: str, chat_id: str = "", source_message_id: str = "", root: Path | None = None) -> dict[str, Any]:
    root = root or get_task_root()
    tid = new_task_id()
    tdir = task_dir(tid, root)
    (tdir / "input" / "4.0").mkdir(parents=True, exist_ok=True)
    (tdir / "input" / "5.1").mkdir(parents=True, exist_ok=True)
    (tdir / "output").mkdir(parents=True, exist_ok=True)
    (tdir / "review").mkdir(parents=True, exist_ok=True)
    bot_dir(tdir)
    from .review_store import create_task_meta

    create_task_meta(tdir, tid, status="created")
    ensure_feishu_meta(tdir, sender_id, chat_id, source_message_id)
    sessions = load_sessions(root)
    sessions[sender_id] = {"task_id": tid, "chat_id": chat_id, "created_at": utc_now_iso(), "updated_at": utc_now_iso()}
    save_sessions(sessions, root)
    atomic_write_json(bot_dir(tdir) / "received_files.json", [])
    return {"task_id": tid, "task_dir": str(tdir)}


def get_active_task_id(sender_id: str, root: Path | None = None) -> str:
    sessions = load_sessions(root)
    return str(sessions.get(sender_id, {}).get("task_id", ""))


def clear_active_session(sender_id: str, root: Path | None = None) -> None:
    sessions = load_sessions(root)
    sessions.pop(sender_id, None)
    save_sessions(sessions, root)


def append_bot_event(task_path: Path, event: dict[str, Any]) -> None:
    line = json.dumps({"time": utc_now_iso(), **event}, ensure_ascii=False)
    path = bot_dir(task_path) / "bot_events.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def record_received_file(task_path: Path, file_info: dict[str, Any]) -> list[dict[str, Any]]:
    path = bot_dir(task_path) / "received_files.json"
    files = read_json(path, [])
    files.append({"received_at": utc_now_iso(), **file_info})
    atomic_write_json(path, files)
    count_40 = sum(1 for item in files if item.get("version") == "4.0")
    count_51 = sum(1 for item in files if item.get("version") == "5.1")
    update_task_meta(task_path, input_40_count=count_40, input_51_count=count_51)
    return files


def scan_task_metas(root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    root = root or get_task_root()
    if not root.exists():
        return []
    result = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        meta = load_task_meta(path)
        if meta:
            result.append((path, meta))
    return result
