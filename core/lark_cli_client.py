"""Thin lark-cli adapter used by the Feishu bot entrypoint.

Only commands already demonstrated by ``shilibot.py`` are considered verified in
this repository. File upload/send and non-image attachment download are kept in
this adapter so they can be verified on the company Windows workstation without
leaking subprocess details into business code.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LarkCliError(RuntimeError):
    pass


class LarkCliClient:
    def __init__(self, cli_path: str | Path | None = None, default_timeout: int = 30) -> None:
        self.cli_path = str(cli_path or os.getenv("LARK_CLI_PATH", "lark-cli"))
        self.default_timeout = default_timeout

    def run_cli(self, *args: str, timeout: int | None = None, expect_json: bool = False) -> str | dict[str, Any] | None:
        cmd = [self.cli_path, *args]
        safe_cmd = ["<LARK_CLI>", *args]
        log.debug("exec: %s", " ".join(safe_cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
                encoding="utf-8",
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("lark-cli timeout after %ss: %s", timeout or self.default_timeout, " ".join(safe_cmd))
            return None
        except OSError as exc:
            log.error("lark-cli failed to start: %s", exc)
            return None
        if result.returncode != 0:
            log.warning("lark-cli failed code=%s cmd=%s stderr=%s", result.returncode, " ".join(safe_cmd), result.stderr.strip())
            return None
        stdout = result.stdout.strip()
        if not expect_json:
            return stdout
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            log.warning("lark-cli returned non-json output for %s", " ".join(safe_cmd))
            return None

    def reply_text(self, message_id: str, text: str) -> bool:
        return self.run_cli("im", "+messages-reply", "--message-id", message_id, "--text", text, "--as", "bot") is not None

    def reply_markdown(self, message_id: str, markdown: str) -> bool:
        return self.run_cli("im", "+messages-reply", "--message-id", message_id, "--markdown", markdown, "--as", "bot") is not None

    def send_text(self, user_id: str | None = None, text: str | None = None, *, chat_id: str | None = None) -> str | None:
        if not text:
            raise LarkCliError("缺少飞书消息文本")
        target_args = _message_target_args(chat_id=chat_id, user_id=user_id)
        safe_target = "--chat-id" if target_args[0] == "--chat-id" else "--user-id"
        log.info("send Feishu text via %s", safe_target)
        data = self.run_cli("im", "+messages-send", *target_args, "--text", text, "--as", "bot", "--format", "json", expect_json=True)
        return _message_id(data)

    def send_markdown(self, user_id: str, markdown: str) -> str | None:
        data = self.run_cli("im", "+messages-send", "--user-id", user_id, "--markdown", markdown, "--as", "bot", "--format", "json", expect_json=True)
        return _message_id(data)

    def get_message_detail(self, message_id: str) -> dict[str, Any] | None:
        data = self.run_cli("im", "+messages-mget", "--message-ids", message_id, "--as", "bot", "--format", "json", expect_json=True)
        if not isinstance(data, dict):
            return None
        messages = data.get("data", {}).get("messages") or data.get("messages") or []
        return messages[0] if messages else None

    def download_message_file(self, message_id: str, file_key: str, output_path: Path, file_type: str = "file") -> Path | None:
        """Download a message attachment/resource to output_path.

        Verified in shilibot.py for image resources using
        ``im +messages-resources-download``. The same command with
        ``--type file`` must be validated on a workstation for Excel/ZIP files.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        old_cwd = Path.cwd()
        try:
            os.chdir(output_path.parent)
            ok = self.run_cli(
                "im", "+messages-resources-download",
                "--message-id", message_id,
                "--file-key", file_key,
                "--type", file_type,
                "--output", output_path.name,
                "--as", "bot",
                timeout=60,
            )
        finally:
            os.chdir(old_cwd)
        if ok is not None and output_path.exists():
            return output_path
        return None

    def upload_file(self, file_path: Path) -> str | None:
        """Upload a local file and return a file key.

        This command is intentionally isolated because the exact lark-cli file
        upload shortcut must be verified on the target workstation.
        """
        file_path = Path(file_path).resolve()
        data = self.run_cli(
            "drive", "+upload",
            "--file", str(file_path),
            "--as", "bot",
            "--format", "json",
            timeout=120,
            expect_json=True,
        )
        if not isinstance(data, dict):
            return None
        return data.get("file_key") or data.get("file_token") or data.get("data", {}).get("file_key") or data.get("data", {}).get("file_token")

    def send_file(self, user_id: str | None = None, file_path: Path | None = None, *, chat_id: str | None = None, timeout: int | None = None) -> str | None:
        """Send a local file to a user.

        First attempts a direct ``im +messages-send --file`` style command. If
        this is unsupported in a local lark-cli version, validate and adjust only
        this adapter.
        """
        if file_path is None:
            raise FileNotFoundError("缺少待发送文件路径")
        file_path = Path(file_path).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        target_args = _message_target_args(chat_id=chat_id, user_id=user_id)
        data = self.run_cli(
            "im", "+messages-send",
            *target_args,
            "--file", str(file_path),
            "--as", "bot",
            "--format", "json",
            timeout=timeout or int(os.getenv("FEISHU_FILE_SEND_TIMEOUT_SECONDS", "120")),
            expect_json=True,
        )
        return _message_id(data)

    def send_progress(self, user_id: str, text: str) -> str | None:
        return self.send_text(user_id, text)

    def update_progress(self, message_id: str, text: str) -> bool:
        """Best-effort update hook; currently returns False unless verified."""
        return False

    def open_event_consumer(self) -> subprocess.Popen:
        """Open the verified lark-cli event consume long-running process."""
        return subprocess.Popen(
            [self.cli_path, "event", "consume", "im.message.receive_v1", "--as", "bot"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )


class FakeLarkCliClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.downloads: dict[str, Path] = {}

    def reply_text(self, message_id: str, text: str) -> bool:
        self.replies.append({"message_id": message_id, "text": text})
        return True

    def reply_markdown(self, message_id: str, markdown: str) -> bool:
        self.replies.append({"message_id": message_id, "markdown": markdown})
        return True

    def send_text(self, user_id: str | None = None, text: str | None = None, *, chat_id: str | None = None) -> str:
        msg = f"fake_msg_{len(self.sent)+1}"
        target_args = _message_target_args(chat_id=chat_id, user_id=user_id)
        target_key = "chat_id" if target_args[0] == "--chat-id" else "user_id"
        self.sent.append({"message_id": msg, target_key: target_args[1], "text": text})
        return msg

    def send_markdown(self, user_id: str, markdown: str) -> str:
        msg = f"fake_msg_{len(self.sent)+1}"
        self.sent.append({"message_id": msg, "user_id": user_id, "markdown": markdown})
        return msg

    def download_message_file(self, message_id: str, file_key: str, output_path: Path, file_type: str = "file") -> Path | None:
        src = self.downloads.get(file_key)
        if not src:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(src.read_bytes())
        return output_path

    def send_file(self, user_id: str | None = None, file_path: Path | None = None, *, chat_id: str | None = None, timeout: int | None = None) -> str:
        msg = f"fake_file_{len(self.sent)+1}"
        target_args = _message_target_args(chat_id=chat_id, user_id=user_id)
        target_key = "chat_id" if target_args[0] == "--chat-id" else "user_id"
        self.sent.append({"message_id": msg, target_key: target_args[1], "file": str(file_path)})
        return msg

    def get_message_detail(self, message_id: str) -> dict[str, Any] | None:
        return None


def _message_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    return data.get("message_id") or data.get("data", {}).get("message_id")


def _message_target_args(*, chat_id: str | None = None, user_id: str | None = None) -> list[str]:
    chat = str(chat_id or "").strip()
    user = str(user_id or "").strip()
    if chat:
        if not chat.startswith("oc_"):
            raise LarkCliError("非法 feishu_chat_id：chat_id 必须以 oc_ 开头")
        return ["--chat-id", chat]
    if user:
        if not user.startswith("ou_"):
            raise LarkCliError("非法 feishu_sender_id：user_id 必须以 ou_ 开头")
        return ["--user-id", user]
    raise LarkCliError("缺少有效飞书接收目标：需要 oc_ chat_id 或 ou_ user_id")
