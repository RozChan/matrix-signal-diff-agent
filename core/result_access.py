"""Secure result-download metadata and task-scoped file allowlisting."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .final_export import FINAL_REVIEW_FILENAME
from .pipeline import OUTPUT_FILENAMES
from .review_store import load_task_meta, update_task_meta


def ensure_result_access(task_dir: Path) -> dict[str, Any]:
    tdir = Path(task_dir)
    meta = load_task_meta(tdir)
    token = str(meta.get("result_token") or secrets.token_urlsafe(32))
    base = os.getenv("REVIEW_BASE_URL", "http://localhost:8501").rstrip("/")
    url = f"{base}/?{urlencode({'view': 'results', 'task_id': tdir.name, 'result_token': token})}"
    return update_task_meta(tdir, result_token=token, result_url=url)


def result_token_valid(task_dir: Path, token: str) -> bool:
    expected = str(load_task_meta(Path(task_dir)).get("result_token") or "")
    return bool(expected and token and secrets.compare_digest(expected, str(token)))


def allowed_result_files(task_dir: Path) -> list[Path]:
    tdir = Path(task_dir).resolve()
    output = (tdir / "output").resolve()
    candidates = [output / FINAL_REVIEW_FILENAME]
    candidates.extend(output / name for name in OUTPUT_FILENAMES.values())
    candidates.extend([tdir / f"全部结果_{tdir.name}.zip", tdir / "selected_pages.json", tdir / "input_manifest.json"])
    allowed = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved != tdir and tdir not in resolved.parents:
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            allowed.append(resolved)
    return allowed


def resolve_allowed_result_file(task_dir: Path, filename: str) -> Path | None:
    if Path(filename).name != filename:
        return None
    return next((path for path in allowed_result_files(task_dir) if path.name == filename), None)
