"""Read-only, header-only IMAP client for trigger detection."""

from __future__ import annotations

import email
import imaplib
import os
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Callable


class ImapMailError(RuntimeError):
    pass


@dataclass(frozen=True)
class MailHeader:
    uid: str
    uidvalidity: str
    message_id: str
    sender_email: str
    sender_display: str
    subject: str
    date: str

    def stable_id(self, mailbox: str, folder: str) -> str:
        return self.message_id.strip() or f"imap:{mailbox.lower()}:{folder}:{self.uidvalidity}:{self.uid}"


def decode_mail_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return str(value)


class ImapMailClient:
    HEADER_QUERY = "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])"

    def __init__(self, connection_factory: Callable[..., imaplib.IMAP4_SSL] | None = None) -> None:
        self.host = os.getenv("MAIL_IMAP_HOST", "").strip()
        self.port = int(os.getenv("MAIL_IMAP_PORT", "993"))
        self.timeout = int(os.getenv("MAIL_IMAP_TIMEOUT_SECONDS", "30"))
        self.username = os.getenv("MAIL_IMAP_USERNAME", "").strip()
        self.password = os.getenv("MAIL_IMAP_PASSWORD", "")
        self.folder = os.getenv("MAIL_IMAP_FOLDER", "INBOX").strip() or "INBOX"
        self.connection_factory = connection_factory or imaplib.IMAP4_SSL
        self.connection: imaplib.IMAP4_SSL | None = None
        if not self.host or not self.username or not self.password:
            raise ImapMailError("IMAP配置不完整：需要host、username和password")

    def connect(self) -> None:
        try:
            try:
                connection = self.connection_factory(self.host, self.port, timeout=self.timeout)
            except TypeError:
                connection = self.connection_factory(self.host, self.port)
            status, _ = connection.login(self.username, self.password)
            if status != "OK":
                raise ImapMailError("IMAP登录失败")
            status, _ = connection.select(self.folder, readonly=True)
            if status != "OK":
                raise ImapMailError("IMAP文件夹只读打开失败")
            self.connection = connection
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ImapMailError(f"IMAP连接或登录失败：{type(exc).__name__}") from exc

    def close(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.close()
        except imaplib.IMAP4.error:
            pass
        try:
            self.connection.logout()
        except imaplib.IMAP4.error:
            pass
        self.connection = None

    def uidvalidity(self) -> str:
        connection = self._connection()
        response = connection.response("UIDVALIDITY")
        values = response[1] if response else []
        if values:
            value = values[0].decode() if isinstance(values[0], bytes) else str(values[0])
            return value.strip()
        status, data = connection.status(self.folder, "(UIDVALIDITY)")
        if status == "OK" and data:
            text = data[0].decode(errors="ignore") if isinstance(data[0], bytes) else str(data[0])
            import re

            match = re.search(r"UIDVALIDITY\s+(\d+)", text)
            if match:
                return match.group(1)
        raise ImapMailError("无法获取UIDVALIDITY")

    def list_uids(self) -> list[str]:
        status, data = self._connection().uid("search", None, "ALL")
        if status != "OK":
            raise ImapMailError("IMAP UID搜索失败")
        raw = data[0] if data else b""
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return [part for part in text.split() if part.isdigit()]

    def fetch_header(self, uid: str, uidvalidity: str) -> MailHeader:
        status, data = self._connection().uid("fetch", str(uid), self.HEADER_QUERY)
        if status != "OK":
            raise ImapMailError(f"邮件UID {uid} 头部读取失败")
        payload = next((item[1] for item in data or [] if isinstance(item, tuple) and isinstance(item[1], bytes)), b"")
        if not payload:
            raise ImapMailError(f"邮件UID {uid} 头部为空")
        message = email.message_from_bytes(payload)
        display, address = parseaddr(decode_mail_header(message.get("From")))
        return MailHeader(
            uid=str(uid),
            uidvalidity=str(uidvalidity),
            message_id=str(message.get("Message-ID") or "").strip(),
            sender_email=address.strip().lower(),
            sender_display=decode_mail_header(display),
            subject=decode_mail_header(message.get("Subject")),
            date=str(message.get("Date") or ""),
        )

    def _connection(self) -> imaplib.IMAP4_SSL:
        if self.connection is None:
            raise ImapMailError("IMAP尚未连接")
        return self.connection

    def __enter__(self) -> "ImapMailClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
