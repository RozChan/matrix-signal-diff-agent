"""Cross-task history for exact field-level human review decisions."""

from __future__ import annotations

import csv
import hashlib
import io
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
    decision_columns = {row["name"] for row in connection.execute("PRAGMA table_info(review_decisions)")}
    if "enabled" not in decision_columns:
        connection.execute("ALTER TABLE review_decisions ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(review_decision_events)")}
    if "action" not in event_columns:
        connection.execute("ALTER TABLE review_decision_events ADD COLUMN action TEXT NOT NULL DEFAULT 'confirm'")
    if "reason" not in event_columns:
        connection.execute("ALTER TABLE review_decision_events ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    connection.commit()
    return connection


def lookup_history_decision(item: dict[str, Any], diff: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = Path(db_path) if db_path is not None else history_database_path()
    if not path.exists():
        return None
    fingerprint = history_fingerprint(item, diff)
    with closing(_connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM review_decisions WHERE fingerprint = ? AND enabled = 1", (fingerprint,)
        ).fetchone()
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
                    enabled = 1,
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


def history_summary(*, db_path: Path) -> dict[str, Any]:
    """Return administrator-facing aggregate counts without exposing database internals."""

    path = Path(db_path)
    empty = {
        "decisions": 0, "enabled": 0, "disabled": 0, "events": 0,
        "same": 0, "different": 0, "descriptions": 0, "units": 0, "latest_confirmed_at": "",
    }
    if not path.exists():
        return empty
    with closing(_connect(path)) as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS decisions,
                      SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                      SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END) AS disabled,
                      SUM(CASE WHEN result = 'same' THEN 1 ELSE 0 END) AS same_count,
                      SUM(CASE WHEN result = 'different' THEN 1 ELSE 0 END) AS different_count,
                      SUM(CASE WHEN diff_field = '信号值描述' THEN 1 ELSE 0 END) AS descriptions,
                      SUM(CASE WHEN diff_field = '单位' THEN 1 ELSE 0 END) AS units,
                      MAX(latest_confirmed_at) AS latest_confirmed_at
               FROM review_decisions"""
        ).fetchone()
        events = int(connection.execute("SELECT COUNT(*) FROM review_decision_events").fetchone()[0])
    return {
        "decisions": int(row["decisions"] or 0), "enabled": int(row["enabled"] or 0),
        "disabled": int(row["disabled"] or 0), "events": events,
        "same": int(row["same_count"] or 0), "different": int(row["different_count"] or 0),
        "descriptions": int(row["descriptions"] or 0), "units": int(row["units"] or 0),
        "latest_confirmed_at": str(row["latest_confirmed_at"] or ""),
    }


def list_history_decisions(
    *, db_path: Path, search: str = "", diff_field: str = "", result: str = "",
    enabled: bool | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    """List current historical decisions using parameterized administrator filters."""

    path = Path(db_path)
    if not path.exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if search.strip():
        pattern = f"%{search.strip()}%"
        clauses.append("(signal_40 LIKE ? OR signal_51 LIKE ? OR value_40 LIKE ? OR value_51 LIKE ? OR latest_task_id LIKE ?)")
        params.extend([pattern] * 5)
    if diff_field:
        clauses.append("diff_field = ?")
        params.append(diff_field)
    if result in {"same", "different"}:
        clauses.append("result = ?")
        params.append(result)
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = min(max(int(limit), 1), 1000)
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM review_decisions{where} ORDER BY latest_confirmed_at DESC LIMIT ?",  # noqa: S608
            (*params, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def history_decision_events(*, db_path: Path, fingerprint: str) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT * FROM review_decision_events WHERE fingerprint = ? ORDER BY id DESC", (fingerprint,)
        ).fetchall()
    return [dict(row) for row in rows]


def admin_update_history_decision(
    *, db_path: Path, fingerprint: str, result: str, actor: str, reason: str,
) -> dict[str, Any]:
    """Correct an effective decision and append a mandatory administrator audit event."""

    if result not in {"same", "different"}:
        raise ValueError("历史结论必须是相同或不同")
    if not reason.strip():
        raise ValueError("管理员修正历史结论时必须填写原因")
    now = utc_now_iso()
    with closing(_connect(Path(db_path))) as connection, connection:
        previous = connection.execute(
            "SELECT * FROM review_decisions WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if previous is None:
            raise KeyError("历史记录不存在")
        connection.execute(
            """UPDATE review_decisions
               SET result = ?, enabled = 1, latest_reviewer = ?, latest_confirmed_at = ?,
                   confirmation_count = confirmation_count + 1
               WHERE fingerprint = ?""",
            (result, actor, now, fingerprint),
        )
        connection.execute(
            """INSERT INTO review_decision_events
               (fingerprint, task_id, reviewer, old_result, new_result, confirmed_at, action, reason)
               VALUES (?, ?, ?, ?, ?, ?, 'admin_correct', ?)""",
            (fingerprint, str(previous["latest_task_id"]), actor, str(previous["result"]), result, now, reason.strip()),
        )
        updated = connection.execute(
            "SELECT * FROM review_decisions WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
    return dict(updated)


def admin_set_history_enabled(
    *, db_path: Path, fingerprint: str, enabled: bool, actor: str, reason: str,
) -> dict[str, Any]:
    """Enable or disable reuse while preserving the decision and its audit trail."""

    if not reason.strip():
        raise ValueError("停用或恢复历史记录时必须填写原因")
    now = utc_now_iso()
    with closing(_connect(Path(db_path))) as connection, connection:
        previous = connection.execute(
            "SELECT * FROM review_decisions WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if previous is None:
            raise KeyError("历史记录不存在")
        connection.execute("UPDATE review_decisions SET enabled = ? WHERE fingerprint = ?", (1 if enabled else 0, fingerprint))
        result = str(previous["result"])
        connection.execute(
            """INSERT INTO review_decision_events
               (fingerprint, task_id, reviewer, old_result, new_result, confirmed_at, action, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fingerprint, str(previous["latest_task_id"]), actor, result, result, now,
                "admin_enable" if enabled else "admin_disable", reason.strip(),
            ),
        )
        updated = connection.execute("SELECT * FROM review_decisions WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return dict(updated)


def export_history_csv(*, db_path: Path, events: bool = False) -> bytes:
    """Export current decisions or immutable audit events as UTF-8 BOM CSV."""

    path = Path(db_path)
    if not path.exists():
        return b"\xef\xbb\xbf"
    table = "review_decision_events" if events else "review_decisions"
    order = "id" if events else "latest_confirmed_at"
    with closing(_connect(path)) as connection:
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY {order} DESC").fetchall()  # noqa: S608
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return output.getvalue().encode("utf-8-sig")
