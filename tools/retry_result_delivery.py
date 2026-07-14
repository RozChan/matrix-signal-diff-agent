"""Retry sending final result files for an existing task."""

from __future__ import annotations

import argparse
import os
import sys

from core.bot_task_store import task_dir
from core.lark_cli_client import LarkCliClient
from core.result_notifier import deliver_results
from core.review_store import load_task_meta, update_task_meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retry final result delivery without rerunning task processing")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--force", action="store_true", help="send even if result_delivery_status is already delivered")
    args = parser.parse_args(argv)

    tdir = task_dir(args.task_id)
    meta = load_task_meta(tdir)
    if not meta:
        print(f"任务不存在：{args.task_id}", file=sys.stderr)
        return 2
    if meta.get("result_delivery_status") in {"sent", "delivered"} and meta.get("status") == "delivered" and not args.force:
        print("结果文件已发送；如需强制补发，请添加 --force。")
        return 0
    if args.force:
        meta = update_task_meta(tdir, result_delivery_status="pending", delivery_error="")
    ok = deliver_results(LarkCliClient(os.getenv("LARK_CLI_PATH", "").strip() or None), tdir, meta)
    if ok:
        print(f"已发送最终结果文件：{args.task_id}")
        return 0
    print(f"最终结果文件发送失败：{load_task_meta(tdir).get('delivery_error', '')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
