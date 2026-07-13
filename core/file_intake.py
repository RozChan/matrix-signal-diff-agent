"""File intake and safety helpers for Feishu-uploaded matrix files."""

from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

DEFAULT_ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".zip"}


def allowed_extensions() -> set[str]:
    raw = os.getenv("BOT_ALLOWED_EXTENSIONS", ".xlsx,.xlsm,.zip")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def max_file_size_bytes() -> int:
    return int(os.getenv("BOT_MAX_FILE_SIZE_MB", "100")) * 1024 * 1024


def max_task_size_bytes() -> int:
    return int(os.getenv("BOT_MAX_TASK_SIZE_MB", "1000")) * 1024 * 1024


def sanitize_filename(name: str) -> str:
    base = Path(name or "uploaded_file").name
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", base).strip(" .")
    return base or "uploaded_file"


def detect_version(filename: str, fallback: str = "") -> str:
    text = filename.lower()
    if "4.0" in text or "40" in text or "4_0" in text:
        return "4.0"
    if "5.1" in text or "51" in text or "5_1" in text:
        return "5.1"
    if fallback in {"4.0", "5.1"}:
        return fallback
    return ""


def validate_extension(path_or_name: str | Path, allowed: Iterable[str] | None = None) -> str:
    suffix = Path(path_or_name).suffix.lower()
    allowed_set = set(allowed or allowed_extensions())
    if suffix not in allowed_set:
        raise ValueError(f"不支持的文件类型：{suffix or '<无后缀>'}，仅支持 {', '.join(sorted(allowed_set))}")
    return suffix


def ensure_size_limit(path: Path, max_bytes: int | None = None) -> None:
    limit = max_bytes or max_file_size_bytes()
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"文件过大：{path.name}，大小 {round(size / 1024 / 1024, 2)}MB，超过限制 {round(limit / 1024 / 1024, 2)}MB")


def task_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def ensure_task_size_limit(task_dir: Path) -> None:
    size = task_size(task_dir)
    limit = max_task_size_bytes()
    if size > limit:
        raise ValueError(f"任务目录总大小超过限制：{round(size / 1024 / 1024, 2)}MB / {round(limit / 1024 / 1024, 2)}MB")


def _safe_zip_member(member_name: str) -> str:
    normalized = Path(member_name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"ZIP 内存在不安全路径：{member_name}")
    return sanitize_filename(normalized.name)


def safe_extract_zip(zip_path: Path, target_dir: Path) -> list[Path]:
    validate_extension(zip_path, {".zip"})
    ensure_size_limit(zip_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_name = _safe_zip_member(info.filename)
            suffix = validate_extension(safe_name, {".xlsx", ".xlsm"})
            if suffix not in {".xlsx", ".xlsm"}:
                continue
            if info.file_size > max_file_size_bytes():
                raise ValueError(f"ZIP 内文件过大：{safe_name}")
            target = target_dir / safe_name
            counter = 1
            while target.exists():
                target = target_dir / f"{target.stem}_{counter}{target.suffix}"
                counter += 1
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


def store_received_file(downloaded_path: Path, task_dir: Path, original_name: str, version: str) -> list[Path]:
    if version not in {"4.0", "5.1"}:
        raise ValueError("无法识别文件版本，请确保文件名包含 4.0 或 5.1，或先发送“添加4.0文件/添加5.1文件”。")
    validate_extension(original_name)
    ensure_size_limit(downloaded_path)
    input_dir = task_dir / "input" / version
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(original_name)
    if Path(safe_name).suffix.lower() == ".zip":
        extracted = safe_extract_zip(downloaded_path, input_dir)
        ensure_task_size_limit(task_dir)
        return extracted
    target = input_dir / safe_name
    counter = 1
    while target.exists():
        target = input_dir / f"{target.stem}_{counter}{target.suffix}"
        counter += 1
    shutil.copy2(downloaded_path, target)
    ensure_task_size_limit(task_dir)
    return [target]
