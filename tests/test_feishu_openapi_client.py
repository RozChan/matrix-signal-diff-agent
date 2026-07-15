from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.feishu_openapi_client import FeishuOpenAPIClient, FeishuOpenAPIError


class Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
    def json(self):
        if self._payload is None:
            raise ValueError("bad json")
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.responses.pop(0)
    def patch(self, url, **kwargs):
        self.calls.append(("patch", url, kwargs))
        return self.responses.pop(0)


def test_token_upload_and_send_file_to_chat(tmp_path: Path) -> None:
    file = tmp_path / "人工审核后最终差异结果.xlsx"
    file.write_bytes(b"excel")
    session = Session([
        Resp(payload={"code": 0, "tenant_access_token": "t-secret", "expire": 7200}),
        Resp(payload={"code": 0, "data": {"file_key": "file-key"}}),
        Resp(payload={"code": 0, "data": {"message_id": "msg-id"}}),
    ])
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=session)
    result = client.send_file(file, chat_id="oc_chat")
    assert result == {"file_name": file.name, "file_key": "file-key", "message_id": "msg-id"}
    assert "/open-apis/auth/v3/tenant_access_token/internal" in session.calls[0][1]
    assert "/open-apis/im/v1/files" in session.calls[1][1]
    assert session.calls[2][2]["params"] == {"receive_id_type": "chat_id"}
    body = session.calls[2][2]["json"]
    assert body["receive_id"] == "oc_chat"
    assert body["msg_type"] == "file"
    assert json.loads(body["content"])["file_key"] == "file-key"


def test_send_file_falls_back_to_open_id(tmp_path: Path) -> None:
    file = tmp_path / "a.xlsx"
    file.write_bytes(b"x")
    session = Session([
        Resp(payload={"code": 0, "tenant_access_token": "token", "expire": 7200}),
        Resp(payload={"code": 0, "data": {"file_key": "fk"}}),
        Resp(payload={"code": 0, "data": {"message_id": "mid"}}),
    ])
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=session)
    client.send_file(file, open_id="ou_user")
    assert session.calls[2][2]["params"] == {"receive_id_type": "open_id"}
    assert session.calls[2][2]["json"]["receive_id"] == "ou_user"


@pytest.mark.parametrize("responses,stage", [
    ([Resp(status_code=500, payload={"err": 1}, text="boom")], "token"),
    ([Resp(payload={"code": 1, "msg": "bad"})], "token"),
])
def test_token_failures(tmp_path: Path, responses, stage) -> None:
    file = tmp_path / "a.xlsx"
    file.write_bytes(b"x")
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=Session(responses))
    with pytest.raises(FeishuOpenAPIError) as exc:
        client.send_file(file, chat_id="oc_chat")
    assert exc.value.stage == stage


def test_upload_failures_and_missing_file_key(tmp_path: Path) -> None:
    file = tmp_path / "a.xlsx"
    file.write_bytes(b"x")
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=Session([
        Resp(payload={"code": 0, "tenant_access_token": "token", "expire": 7200}),
        Resp(payload={"code": 0, "data": {}}),
    ]))
    with pytest.raises(FeishuOpenAPIError) as exc:
        client.send_file(file, chat_id="oc_chat")
    assert exc.value.stage == "upload"


def test_send_message_business_failure(tmp_path: Path) -> None:
    file = tmp_path / "a.xlsx"
    file.write_bytes(b"x")
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=Session([
        Resp(payload={"code": 0, "tenant_access_token": "token", "expire": 7200}),
        Resp(payload={"code": 0, "data": {"file_key": "fk"}}),
        Resp(payload={"code": 999, "msg": "send bad"}),
    ]))
    with pytest.raises(FeishuOpenAPIError) as exc:
        client.send_file(file, chat_id="oc_chat")
    assert exc.value.stage == "send_message"


def test_file_validation_prevents_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.xlsx"
    session = Session([])
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=session)
    with pytest.raises(FeishuOpenAPIError):
        client.send_file(missing, chat_id="oc_chat")
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    with pytest.raises(FeishuOpenAPIError):
        client.send_file(empty, chat_id="oc_chat")
    assert session.calls == []


def test_progress_card_send_and_update(tmp_path: Path) -> None:
    session = Session([
        Resp(payload={"code": 0, "tenant_access_token": "token", "expire": 7200}),
        Resp(payload={"code": 0, "data": {"message_id": "msg-card"}}),
        Resp(payload={"code": 0, "data": {}}),
    ])
    client = FeishuOpenAPIClient(app_id="app", app_secret="secret", session=session)
    card = {"config": {"wide_screen_mode": True}, "elements": []}
    assert client.send_progress_card(card, chat_id="oc_chat") == "msg-card"
    client.update_progress_card("msg-card", card)
    assert session.calls[1][2]["json"]["msg_type"] == "interactive"
    assert session.calls[1][2]["params"] == {"receive_id_type": "chat_id"}
    assert session.calls[2][0] == "patch"
    assert "/open-apis/im/v1/messages/msg-card" in session.calls[2][1]
    assert session.calls[2][2]["json"]["msg_type"] == "interactive"
