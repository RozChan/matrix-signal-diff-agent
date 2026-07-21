"""Diagnose or test Feishu Open API file delivery for a task."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.bot_task_store import task_dir
from core.feishu_openapi_client import FeishuOpenAPIClient, FeishuOpenAPIError, _resolve_receive_target
from core.final_export import FINAL_REVIEW_FILENAME
from core.result_notifier import deliver_results
from core.review_store import load_task_meta, update_task_meta


def _final_file(tdir: Path) -> Path:
    return tdir / "output" / FINAL_REVIEW_FILENAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose or send Feishu final result files")
    parser.add_argument("--task-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)
    tdir = task_dir(args.task_id)
    meta = load_task_meta(tdir)
    if not meta:
        print(f"任务不存在：{args.task_id}", file=sys.stderr)
        return 2
    final_path = _final_file(tdir)
    print(f"FEISHU_APP_ID configured: {bool(os.getenv('FEISHU_APP_ID'))}")
    print(f"FEISHU_APP_SECRET configured: {bool(os.getenv('FEISHU_APP_SECRET'))}")
    print(f"send mode: {os.getenv('FEISHU_FILE_SEND_MODE', 'openapi')}")
    print(f"final file exists: {final_path.exists()}")
    print(f"final file size: {final_path.stat().st_size if final_path.exists() else 0}")
    try:
        receive_type, receive_id = _resolve_receive_target(chat_id=meta.get("feishu_chat_id"), open_id=meta.get("feishu_sender_id"))
        print(f"target: {receive_type} {receive_id}")
    except FeishuOpenAPIError as exc:
        print(f"target error: {exc}", file=sys.stderr)
        return 2 if args.dry_run else 1
    if args.dry_run:
        return 0
    update_task_meta(tdir, result_delivery_status="pending", delivery_error="")
    ok = deliver_results(FeishuOpenAPIClient(), tdir, load_task_meta(tdir), force=True)
    print("send result: success" if ok else f"send result: failed {load_task_meta(tdir).get('delivery_error', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
