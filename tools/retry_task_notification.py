"""Retry sending a Feishu human-review notification for an existing task."""

from __future__ import annotations

import argparse
import os
import sys

from core.bot_task_store import task_dir
from core.lark_cli_client import LarkCliClient
from core.result_notifier import notify_review_ready
from core.review_store import load_task_meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retry a task's Feishu review notification without rerunning processing")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--force", action="store_true", help="send even if notification_status is already sent")
    args = parser.parse_args(argv)

    tdir = task_dir(args.task_id)
    meta = load_task_meta(tdir)
    if not meta:
        print(f"任务不存在：{args.task_id}", file=sys.stderr)
        return 2
    if meta.get("status") != "awaiting_review":
        print(f"任务状态不是 awaiting_review，当前状态：{meta.get('status', '')}", file=sys.stderr)
        return 2
    if meta.get("notification_status") == "sent" and not args.force:
        print("通知已发送；如需强制补发，请添加 --force。")
        return 0
    if not meta.get("review_url"):
        print("任务缺少 review_url，无法补发。", file=sys.stderr)
        return 2
    if not (meta.get("feishu_chat_id") or meta.get("feishu_sender_id")):
        print("任务缺少 feishu_chat_id/feishu_sender_id，无法补发。", file=sys.stderr)
        return 2

    cli_path = os.getenv("LARK_CLI_PATH", "").strip() or None
    ok = notify_review_ready(LarkCliClient(cli_path), tdir, meta, force=args.force)
    if ok:
        print(f"已补发人工审核链接：{args.task_id}")
        return 0
    print(f"补发人工审核链接失败：{args.task_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
