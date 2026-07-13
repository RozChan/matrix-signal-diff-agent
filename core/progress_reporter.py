"""Throttled progress reporting for Feishu tasks."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProgressReporter:
    client: Any
    user_id: str
    min_interval_seconds: int = field(default_factory=lambda: int(os.getenv("BOT_PROGRESS_MIN_INTERVAL_SECONDS", "15")))
    last_sent_at: float = 0.0
    last_text: str = ""
    last_ai_bucket: int = -1

    def send(self, text: str, force: bool = False) -> bool:
        now = time.time()
        if not force and text == self.last_text:
            return False
        if not force and now - self.last_sent_at < self.min_interval_seconds:
            return False
        self.client.send_text(self.user_id, text)
        self.last_sent_at = now
        self.last_text = text
        return True

    def stage(self, task_id: str, stage: str, progress: int = 0, force: bool = False) -> bool:
        return self.send(f"任务编号：{task_id}\n当前阶段：{stage}\n进度：{progress}%", force=force)

    def ai(self, task_id: str, payload: dict[str, Any], ai_required_total: int) -> bool:
        current = int(payload.get("ai_completed") or 0)
        failed = int(payload.get("failed") or 0)
        total = max(ai_required_total, 0)
        bucket = 100 if total == 0 else int(current / total * 10)
        should_send = current == total or current % 5 == 0 or bucket != self.last_ai_bucket
        if not should_send:
            return False
        self.last_ai_bucket = bucket
        return self.send(
            f"任务编号：{task_id}\n信号级AI辅助复核中\nAI复核进度：{current} / {total}\n当前信号：{payload.get('signal_name', '')}\n失败：{failed}",
            force=current == total,
        )
