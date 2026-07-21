"""Shared per-task reentrant locks for state files."""

from __future__ import annotations

import threading
from pathlib import Path

_TASK_LOCKS: dict[str, threading.RLock] = {}
_TASK_LOCKS_GUARD = threading.Lock()


def get_task_lock(task_dir: Path) -> threading.RLock:
    key = str(Path(task_dir).resolve())
    with _TASK_LOCKS_GUARD:
        lock = _TASK_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _TASK_LOCKS[key] = lock
        return lock
