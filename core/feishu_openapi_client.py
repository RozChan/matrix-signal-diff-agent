"""Feishu Open API client for uploading and sending local files."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


class FeishuOpenAPIError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class FeishuOpenAPIClient:
    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.app_id = app_id if app_id is not None else os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret if app_secret is not None else os.getenv("FEISHU_APP_SECRET", "")
        self.base_url = (base_url or os.getenv("FEISHU_OPENAPI_BASE_URL", "https://open.feishu.cn")).rstrip("/")
        self.session = session or requests.Session()
        self._tenant_access_token = ""
        self._token_expires_at = 0.0
        self.upload_timeout = int(os.getenv("FEISHU_FILE_UPLOAD_TIMEOUT_SECONDS", "120"))
        self.send_timeout = int(os.getenv("FEISHU_FILE_SEND_TIMEOUT_SECONDS", "60"))
        self.max_file_size = int(os.getenv("FEISHU_MAX_FILE_SIZE_MB", "30")) * 1024 * 1024

    def _require_config(self) -> None:
        if not self.app_id:
            raise FeishuOpenAPIError("token", "缺少 FEISHU_APP_ID")
        if not self.app_secret:
            raise FeishuOpenAPIError("token", "缺少 FEISHU_APP_SECRET")

    def _json_request(self, method: str, path: str, payload: dict[str, Any], *, params: dict[str, str] | None = None, token: str | None = None, timeout: int | None = None, stage: str = "send_message") -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = getattr(self.session, method.lower())
        response = request(f"{self.base_url}{path}", params=params, headers=headers, json=payload, timeout=timeout or self.send_timeout)
        if response.status_code < 200 or response.status_code >= 300:
            raise FeishuOpenAPIError(stage, f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise FeishuOpenAPIError(stage, "飞书返回非JSON内容") from exc
        return data

    def _post_json(self, path: str, payload: dict[str, Any], *, params: dict[str, str] | None = None, token: str | None = None, timeout: int | None = None) -> dict[str, Any]:
        return self._json_request("post", path, payload, params=params, token=token, timeout=timeout, stage="send_message")

    def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expires_at - 60:
            return self._tenant_access_token
        self._require_config()
        response = self.session.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise FeishuOpenAPIError("token", f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise FeishuOpenAPIError("token", "飞书凭证接口返回非JSON内容") from exc
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("token", f"飞书凭证接口错误：code={data.get('code')} msg={data.get('msg') or data.get('message')}")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuOpenAPIError("token", "飞书凭证接口未返回tenant_access_token")
        self._tenant_access_token = token
        self._token_expires_at = now + int(data.get("expire") or 7200)
        return token

    def _validate_file(self, file_path: Path) -> Path:
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FeishuOpenAPIError("upload", f"文件不存在：{path}")
        size = path.stat().st_size
        if size <= 0:
            raise FeishuOpenAPIError("upload", f"文件为空：{path.name}")
        if size > self.max_file_size:
            raise FeishuOpenAPIError("upload", f"文件超过大小限制：{path.name}")
        return path

    def upload_file(self, file_path: Path, *, file_type: str = "stream") -> str:
        path = self._validate_file(file_path)
        token = self.get_tenant_access_token()
        with path.open("rb") as fh:
            response = self.session.post(
                f"{self.base_url}/open-apis/im/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                data={"file_type": file_type, "file_name": path.name},
                files={"file": (path.name, fh)},
                timeout=self.upload_timeout,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise FeishuOpenAPIError("upload", f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise FeishuOpenAPIError("upload", "飞书文件上传接口返回非JSON内容") from exc
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("upload", f"飞书文件上传失败：code={data.get('code')} msg={data.get('msg') or data.get('message')}")
        file_key = str((data.get("data") or {}).get("file_key") or data.get("file_key") or "")
        if not file_key:
            raise FeishuOpenAPIError("upload", "飞书文件上传响应缺少file_key")
        return file_key

    def send_file_message(self, *, file_key: str, chat_id: str | None = None, open_id: str | None = None) -> str:
        receive_id_type, receive_id = _resolve_receive_target(chat_id=chat_id, open_id=open_id)
        token = self.get_tenant_access_token()
        data = self._post_json(
            "/open-apis/im/v1/messages",
            {"receive_id": receive_id, "msg_type": "file", "content": json.dumps({"file_key": file_key}, ensure_ascii=False)},
            params={"receive_id_type": receive_id_type},
            token=token,
            timeout=self.send_timeout,
        )
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("send_message", f"飞书文件消息发送失败：code={data.get('code')} msg={data.get('msg') or data.get('message')}")
        message_id = str((data.get("data") or {}).get("message_id") or data.get("message_id") or "")
        if not message_id:
            raise FeishuOpenAPIError("send_message", "飞书文件消息响应缺少message_id")
        return message_id

    def send_progress_card(self, card: dict[str, Any], *, chat_id: str | None = None, open_id: str | None = None) -> str:
        receive_id_type, receive_id = _resolve_receive_target(chat_id=chat_id, open_id=open_id)
        token = self.get_tenant_access_token()
        data = self._post_json(
            "/open-apis/im/v1/messages",
            {"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            params={"receive_id_type": receive_id_type},
            token=token,
            timeout=self.send_timeout,
        )
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("send_message", f"飞书进度卡片发送失败：code={data.get('code')} msg={data.get('msg') or data.get('message')}")
        message_id = str((data.get("data") or {}).get("message_id") or data.get("message_id") or "")
        if not message_id:
            raise FeishuOpenAPIError("send_message", "飞书进度卡片响应缺少message_id")
        return message_id

    def update_progress_card(self, message_id: str, card: dict[str, Any]) -> None:
        if not message_id:
            raise FeishuOpenAPIError("update_message", "缺少待更新的飞书消息ID")
        token = self.get_tenant_access_token()
        data = self._json_request(
            "patch",
            f"/open-apis/im/v1/messages/{message_id}",
            {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            token=token,
            timeout=self.send_timeout,
            stage="update_message",
        )
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("update_message", f"飞书进度卡片更新失败：code={data.get('code')} msg={data.get('msg') or data.get('message')}")

    def send_text(self, user_id: str | None = None, text: str | None = None, *, chat_id: str | None = None) -> str:
        if not text:
            raise FeishuOpenAPIError("send_message", "缺少飞书文本消息内容")
        receive_id_type, receive_id = _resolve_receive_target(chat_id=chat_id, open_id=user_id)
        token = self.get_tenant_access_token()
        data = self._post_json(
            "/open-apis/im/v1/messages",
            {"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            params={"receive_id_type": receive_id_type},
            token=token,
            timeout=self.send_timeout,
        )
        if int(data.get("code") or 0) != 0:
            raise FeishuOpenAPIError("send_message", f"飞书文本消息发送失败：code={data.get('code')} msg={data.get('msg') or data.get('message')}")
        message_id = str((data.get("data") or {}).get("message_id") or data.get("message_id") or "")
        if not message_id:
            raise FeishuOpenAPIError("send_message", "飞书文本消息响应缺少message_id")
        return message_id

    def send_file(self, file_path: Path, *, chat_id: str | None = None, open_id: str | None = None) -> dict[str, str]:
        path = self._validate_file(file_path)
        file_key = self.upload_file(path)
        message_id = self.send_file_message(file_key=file_key, chat_id=chat_id, open_id=open_id)
        return {"file_name": path.name, "file_key": file_key, "message_id": message_id}


def _resolve_receive_target(*, chat_id: str | None = None, open_id: str | None = None) -> tuple[str, str]:
    chat = str(chat_id or "").strip()
    user = str(open_id or "").strip()
    if chat:
        if not chat.startswith("oc_"):
            raise FeishuOpenAPIError("send_message", "非法 feishu_chat_id：chat_id 必须以 oc_ 开头")
        return "chat_id", chat
    if user:
        if not user.startswith("ou_"):
            raise FeishuOpenAPIError("send_message", "非法 feishu_sender_id：open_id 必须以 ou_ 开头")
        return "open_id", user
    raise FeishuOpenAPIError("send_message", "缺少有效飞书接收目标：需要 oc_ chat_id 或 ou_ open_id")
