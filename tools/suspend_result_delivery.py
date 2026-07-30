"""Suspend automatic final result delivery for an existing task."""

from __future__ import annotations

import argparse
import sys

from core.bot_task_store import task_dir
from core.review_store import load_task_meta, update_task_meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suspend automatic result delivery for a task")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    tdir = task_dir(args.task_id)
    if not load_task_meta(tdir):
        print(f"任务不存在：{args.task_id}", file=sys.stderr)
        return 2
    update_task_meta(
        tdir,
        result_delivery_status="failed",
        result_delivery_auto_retry_exhausted=True,
        result_delivery_next_retry_at="",
        delivery_error="自动交付已暂停，等待人工补发",
    )
    print(f"已暂停自动结果交付：{args.task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
