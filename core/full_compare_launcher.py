"""Schedule an already-created full compare through the proven bot pipeline."""

from __future__ import annotations

import threading
from typing import Any

from .full_compare_task import FullCompareTaskResult
from .notification_router import notify_task_started


class NoopEnterpriseClient:
    """Custom-bot tasks intentionally do not use enterprise-app messaging."""

    def send_progress_card(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def update_progress_card(self, *args: Any, **kwargs: Any) -> None:
        return None

    def send_text(self, *args: Any, **kwargs: Any) -> None:
        return None


def launch_full_compare_task(result: FullCompareTaskResult, *, client: Any | None = None) -> None:
    from bot_service import _download_confluence_source
    from .confluence_task_store import load_confluence_sources

    runtime_client = client or NoopEnterpriseClient()
    notify_task_started(result.task_dir)
    sources = list(result.sources)
    if result.duplicate:
        persisted = load_confluence_sources(result.task_dir, result.task_id)
        sources = [item for item in persisted.get("sources", []) if item.get("status") == "pending"]
    for source in sources:
        threading.Thread(
            target=_download_confluence_source,
            args=(result.task_id, result.task_dir, dict(source), runtime_client, ""),
            daemon=True,
        ).start()


def recover_custom_full_compare_tasks(*, client: Any | None = None) -> int:
    """Resume unfinished custom-bot downloads without touching enterprise tasks."""

    from bot_service import _download_confluence_source, _maybe_auto_start
    from .bot_task_store import scan_task_metas
    from .confluence_task_store import load_confluence_sources, update_source

    runtime_client = client or NoopEnterpriseClient()
    resumed_count = 0
    for tdir, meta in scan_task_metas():
        if meta.get("notify_type") != "feishu_custom_bot" or meta.get("status") not in {"created", "downloading"}:
            continue
        data = load_confluence_sources(tdir, tdir.name)
        pending = []
        for source in data.get("sources", []):
            if source.get("status") in {"pending", "scanning", "downloading"}:
                update_source(tdir, source.get("url", ""), status="pending", errors=[])
                pending.append({**source, "status": "pending", "errors": []})
        for source in pending:
            threading.Thread(target=_download_confluence_source, args=(tdir.name, tdir, source, runtime_client, ""), daemon=True).start()
            resumed_count += 1
        if not pending:
            _maybe_auto_start(tdir.name, tdir, runtime_client, "")
    return resumed_count
