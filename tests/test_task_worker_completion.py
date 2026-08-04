from __future__ import annotations

from pathlib import Path

from core.review_store import create_task_meta, load_task_meta
from core.task_worker import run_task


def test_worker_records_failed_ai_calls_as_processed_and_dispatches_review_notice(
    tmp_path: Path, monkeypatch
) -> None:
    task_path = tmp_path / "task-worker"
    for version in ("4.0", "5.1"):
        input_dir = task_path / "input" / version
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / f"{version}.xlsx").write_bytes(b"xlsx")
    create_task_meta(task_path, task_path.name)
    compare_path = task_path / "output" / "compare.xlsx"
    compare_path.parent.mkdir(parents=True, exist_ok=True)
    compare_path.write_bytes(b"xlsx")

    monkeypatch.setattr("core.task_worker.task_dir", lambda _task_id: task_path)
    monkeypatch.setattr(
        "core.task_worker.run_all",
        lambda *_args, **_kwargs: {"files": {"compare": compare_path}},
    )
    monkeypatch.setattr(
        "core.task_worker.run_ai_review",
        lambda *_args, **_kwargs: {
            "ai_called_count": 25,
            "ai_reviewed_count": 21,
            "ai_failed_count": 4,
        },
    )
    monkeypatch.setattr(
        "core.task_worker.generate_review_items_from_excel",
        lambda *_args, **_kwargs: [{"item_id": "one"}],
    )
    monkeypatch.setattr("core.task_worker.init_review_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "core.task_worker.compute_review_stats",
        lambda *_args, **_kwargs: {"pending_manual": 4, "history_reused": 5},
    )
    monkeypatch.setattr(
        "core.task_worker.set_review_link",
        lambda _task_path: {"review_url": "https://review/task-worker"},
    )
    notices: list[Path] = []
    monkeypatch.setattr(
        "core.notification_router.notify_review_ready",
        lambda path: notices.append(Path(path)) or True,
    )

    result = run_task(task_path.name)

    meta = load_task_meta(task_path)
    assert meta["status"] == "awaiting_review"
    assert meta["ai_required_signal_count"] == 25
    assert meta["ai_completed_signal_count"] == 25
    assert meta["ai_failed_signal_count"] == 4
    assert notices == [task_path]
    assert result["review_stats"]["pending_manual"] == 4


def test_worker_does_not_send_review_notice_without_pending_items(
    tmp_path: Path, monkeypatch
) -> None:
    task_path = tmp_path / "task-no-pending"
    for version in ("4.0", "5.1"):
        input_dir = task_path / "input" / version
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / f"{version}.xlsx").write_bytes(b"xlsx")
    create_task_meta(task_path, task_path.name)
    compare_path = task_path / "output" / "compare.xlsx"
    compare_path.parent.mkdir(parents=True, exist_ok=True)
    compare_path.write_bytes(b"xlsx")

    monkeypatch.setattr("core.task_worker.task_dir", lambda _task_id: task_path)
    monkeypatch.setattr(
        "core.task_worker.run_all",
        lambda *_args, **_kwargs: {"files": {"compare": compare_path}},
    )
    monkeypatch.setattr(
        "core.task_worker.run_ai_review",
        lambda *_args, **_kwargs: {
            "ai_called_count": 0,
            "ai_reviewed_count": 0,
            "ai_failed_count": 0,
        },
    )
    monkeypatch.setattr(
        "core.task_worker.generate_review_items_from_excel",
        lambda *_args, **_kwargs: [{"item_id": "one"}],
    )
    monkeypatch.setattr("core.task_worker.init_review_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "core.task_worker.compute_review_stats",
        lambda *_args, **_kwargs: {"pending_manual": 0, "history_reused": 1},
    )
    monkeypatch.setattr(
        "core.task_worker.set_review_link",
        lambda _task_path: {"review_url": "https://review/task-no-pending"},
    )
    notices: list[Path] = []
    monkeypatch.setattr(
        "core.notification_router.notify_review_ready",
        lambda path: notices.append(Path(path)) or True,
    )

    run_task(task_path.name)

    assert notices == []
