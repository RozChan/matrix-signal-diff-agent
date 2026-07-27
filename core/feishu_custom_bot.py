"""One-way Feishu group custom-bot webhook client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import requests


class FeishuCustomBotError(RuntimeError):
    pass


def generate_signature(timestamp: int | str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class FeishuCustomBotClient:
    def __init__(self, webhook: str | None = None, secret: str | None = None, session: requests.Session | None = None) -> None:
        if webhook is None and os.getenv("FEISHU_CUSTOM_BOT_ENABLED", "false").strip().lower() != "true":
            raise FeishuCustomBotError("飞书自定义机器人未启用")
        self.webhook = (webhook if webhook is not None else os.getenv("FEISHU_CUSTOM_BOT_WEBHOOK", "")).strip()
        self.secret = secret if secret is not None else os.getenv("FEISHU_CUSTOM_BOT_SECRET", "").strip()
        self.timeout = int(os.getenv("FEISHU_CUSTOM_BOT_TIMEOUT_SECONDS", "10"))
        self.max_attempts = max(1, int(os.getenv("FEISHU_CUSTOM_BOT_MAX_ATTEMPTS", "3")))
        self.session = session or requests.Session()
        if not self.webhook.startswith("https://"):
            raise FeishuCustomBotError("飞书自定义机器人Webhook未配置或不是HTTPS地址")

    def send_card(self, title: str, markdown: str, *, button_text: str = "", button_url: str = "") -> None:
        elements: list[dict[str, Any]] = [{"tag": "markdown", "content": markdown}]
        if button_text and button_url:
            elements.append({"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": button_text}, "type": "primary", "url": button_url}]})
        card = {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"}, "elements": elements}
        self._send({"msg_type": "interactive", "card": card})

    def _send(self, payload: dict[str, Any]) -> None:
        if self.secret:
            timestamp = int(time.time())
            payload = {**payload, "timestamp": str(timestamp), "sign": generate_signature(timestamp, self.secret)}
        last_error = ""
        for attempt in range(self.max_attempts):
            try:
                response = self.session.post(self.webhook, json=payload, timeout=self.timeout)
                if response.status_code < 200 or response.status_code >= 300:
                    raise FeishuCustomBotError(f"Webhook HTTP {response.status_code}")
                data = response.json()
                code = int(data.get("code", data.get("StatusCode", 0)) or 0)
                if code != 0:
                    raise FeishuCustomBotError(f"Webhook业务错误 code={code} msg={data.get('msg') or data.get('StatusMessage') or ''}")
                return
            except (requests.RequestException, ValueError, FeishuCustomBotError) as exc:
                last_error = str(exc).replace(self.webhook, "<webhook-redacted>")
                if self.secret:
                    last_error = last_error.replace(self.secret, "<secret-redacted>")
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(2**attempt, 4))
        raise FeishuCustomBotError(last_error or "飞书自定义机器人通知失败")
