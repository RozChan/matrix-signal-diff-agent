"""Cross-task history for exact field-level human review decisions."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HISTORY_SCHEMA_VERSION = 1
HISTORY_DECISION_SOURCE = "history_manual"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def history_database_path(review_dir: Path | None = None) -> Path:
    """Return the shared history DB, outside individual task directories."""

    configured = os.getenv("REVIEW_HISTORY_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if review_dir is not None:
        task_dir = Path(review_dir).parent if Path(review_dir).name == "review" else Path(review_dir)
        return task_dir.parent / "review_history.sqlite3"
    task_root = Path(os.getenv("TASK_ROOT_DIR", "temp")).expanduser().resolve()
    return task_root / "review_history.sqlite3"


def normalize_history_text(value: Any) -> str:
    """Apply only storage-safe normalization; do not perform semantic matching."""

    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def history_identity(item: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "source_sheet": normalize_history_text(item.get("source_sheet")),
        "signal_40": normalize_history_text(item.get("signal_40")),
        "signal_51": normalize_history_text(item.get("signal_51")),
        "diff_field": normalize_history_text(diff.get("diff_field")),
        "value_40": normalize_history_text(diff.get("value_40")),
        "value_51": normalize_history_text(diff.get("value_51")),
    }


def history_fingerprint(item: dict[str, Any], diff: dict[str, Any]) -> str:
    payload = json.dumps(history_identity(item, diff), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_decisions (
            fingerprint TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            source_sheet TEXT NOT NULL,
            signal_40 TEXT NOT NULL,
            signal_51 TEXT NOT NULL,
            diff_field TEXT NOT NULL,
            value_40 TEXT NOT NULL,
            value_51 TEXT NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('same', 'different')),
            first_task_id TEXT NOT NULL,
            latest_task_id TEXT NOT NULL,
            first_reviewer TEXT NOT NULL DEFAULT '',
            latest_reviewer TEXT NOT NULL DEFAULT '',
            first_confirmed_at TEXT NOT NULL,
            latest_confirmed_at TEXT NOT NULL,
            confirmation_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS review_decision_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL,
            task_id TEXT NOT NULL,
            reviewer TEXT NOT NULL DEFAULT '',
            old_result TEXT NOT NULL DEFAULT '',
            new_result TEXT NOT NULL,
            confirmed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_review_history_signal
            ON review_decisions(signal_40, signal_51, diff_field);
        """
    )
    return connection


def lookup_history_decision(item: dict[str, Any], diff: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = Path(db_path) if db_path is not None else history_database_path()
    if not path.exists():
        return None
    fingerprint = history_fingerprint(item, diff)
    with closing(_connect(path)) as connection:
        row = connection.execute("SELECT * FROM review_decisions WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return dict(row) if row is not None else None


def record_history_decisions(
    decisions: Iterable[tuple[dict[str, Any], dict[str, Any], str]],
    *,
    task_id: str,
    reviewer: str,
    db_path: Path,
) -> int:
    """Atomically upsert explicit human decisions; the latest save is authoritative."""

    rows = list(decisions)
    if not rows:
        return 0
    now = utc_now_iso()
    with closing(_connect(Path(db_path))) as connection, connection:
        for item, diff, result in rows:
            if result not in {"same", "different"}:
                raise ValueError(f"不支持的历史审核结果：{result}")
            identity = history_identity(item, diff)
            fingerprint = history_fingerprint(item, diff)
            previous = connection.execute(
                "SELECT result FROM review_decisions WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            old_result = str(previous["result"]) if previous else ""
            connection.execute(
                """
                INSERT INTO review_decisions (
                    fingerprint, schema_version, source_sheet, signal_40, signal_51,
                    diff_field, value_40, value_51, result, first_task_id,
                    latest_task_id, first_reviewer, latest_reviewer,
                    first_confirmed_at, latest_confirmed_at, confirmation_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    result = excluded.result,
                    latest_task_id = excluded.latest_task_id,
                    latest_reviewer = excluded.latest_reviewer,
                    latest_confirmed_at = excluded.latest_confirmed_at,
                    confirmation_count = review_decisions.confirmation_count + 1
                """,
                (
                    fingerprint, identity["schema_version"], identity["source_sheet"], identity["signal_40"],
                    identity["signal_51"], identity["diff_field"], identity["value_40"], identity["value_51"],
                    result, task_id, task_id, reviewer, reviewer, now, now,
                ),
            )
            connection.execute(
                """INSERT INTO review_decision_events
                   (fingerprint, task_id, reviewer, old_result, new_result, confirmed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fingerprint, task_id, reviewer, old_result, result, now),
            )
    return len(rows)


def history_counts(*, db_path: Path) -> dict[str, int]:
    if not Path(db_path).exists():
        return {"decisions": 0, "events": 0}
    with closing(_connect(Path(db_path))) as connection:
        decisions = int(connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0])
        events = int(connection.execute("SELECT COUNT(*) FROM review_decision_events").fetchone()[0])
    return {"decisions": decisions, "events": events}
