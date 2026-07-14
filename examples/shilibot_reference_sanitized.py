"""Sanitized lark-cli Feishu bot reference.

This file intentionally removes real local paths, open_ids, document tokens and
internal URLs from the original local-only ``shilibot.py`` reference. It keeps
only the technical structure used by this project: lark-cli subprocess calls,
event consume, threaded message handling, replies, direct sends and attachment
resource downloads.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

LARK_CLI = os.getenv("LARK_CLI_PATH", "lark-cli")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
log = logging.getLogger("sanitized-lark-reference")


def run_cli(*args: str, timeout: int = 30) -> str | None:
    """Run lark-cli without shell=True and return stdout on success."""
    result = subprocess.run([LARK_CLI, *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout, check=False)
    if result.returncode != 0:
        log.warning("lark-cli failed: %s", result.stderr.strip())
        return None
    return result.stdout.strip()


def reply_text(message_id: str, text: str) -> bool:
    return run_cli("im", "+messages-reply", "--message-id", message_id, "--text", text, "--as", "bot") is not None


def send_text(user_id: str, text: str) -> str | None:
    output = run_cli("im", "+messages-send", "--user-id", user_id, "--text", text, "--as", "bot", "--format", "json")
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    return data.get("message_id") or data.get("data", {}).get("message_id")


def download_message_resource(message_id: str, file_key: str, output_path: Path, resource_type: str = "file") -> Path | None:
    """Reference pattern based on shilibot.py image download usage."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    old_cwd = Path.cwd()
    try:
        os.chdir(output_path.parent)
        ok = run_cli(
            "im", "+messages-resources-download",
            "--message-id", message_id,
            "--file-key", file_key,
            "--type", resource_type,
            "--output", output_path.name,
            "--as", "bot",
            timeout=30,
        )
    finally:
        os.chdir(old_cwd)
    return output_path if ok and output_path.exists() else None


def handle_message(event: dict) -> None:
    message_id = event.get("message_id", "")
    content = event.get("content", "")
    if message_id:
        reply_text(message_id, f"收到消息：{content[:50]}")


def main() -> None:
    consume_cmd = [LARK_CLI, "event", "consume", "im.message.receive_v1", "--as", "bot"]
    proc = subprocess.Popen(consume_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                log.info("[event-consume] %s", line.strip())

    threading.Thread(target=read_stderr, daemon=True).start()
    if proc.stdin:
        proc.stdin.write("\n")
        proc.stdin.flush()
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        threading.Thread(target=handle_message, args=(event,), daemon=True).start()


if __name__ == "__main__":
    main()
