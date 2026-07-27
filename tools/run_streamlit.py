"""Start Streamlit from the single public URL configured in ``.env``."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_review_base_url(value: str) -> tuple[str, int, str]:
    """Return public host, port and normalized URL for a direct Streamlit URL."""

    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("REVIEW_BASE_URL 必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("REVIEW_BASE_URL 不能包含账号、查询参数或锚点")
    if parsed.path not in {"", "/"}:
        raise ValueError("REVIEW_BASE_URL 只能配置站点根地址，不能包含页面路径")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("REVIEW_BASE_URL 端口无效") from exc
    return parsed.hostname, port, raw


def streamlit_command(host: str, port: int) -> list[str]:
    """Build the command without duplicating public host or port in another config file."""

    return [
        sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "app.py"),
        "--server.address", "0.0.0.0",
        "--server.port", str(port),
        "--browser.serverAddress", host,
        "--browser.serverPort", str(port),
        "--server.headless", "true",
    ]


def _open_admin_when_ready(public_url: str, port: int, timeout_seconds: float = 30) -> None:
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    webbrowser.open(f"{public_url}/?view=admin")
                    return
        except OSError:
            time.sleep(0.25)


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    configured = os.getenv("REVIEW_BASE_URL", "").strip()
    if not configured:
        print("启动失败：请在 .env 中配置 REVIEW_BASE_URL，例如 http://10.105.194.152:8501", file=sys.stderr)
        return 2
    try:
        host, port, public_url = parse_review_base_url(configured)
    except ValueError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    admin_url = f"{public_url}/?view=admin"
    print(f"启动 Streamlit 内网审核服务：{admin_url}", flush=True)
    threading.Thread(target=_open_admin_when_ready, args=(public_url, port), daemon=True).start()
    try:
        return subprocess.call(streamlit_command(host, port), cwd=REPO_ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
