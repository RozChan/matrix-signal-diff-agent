"""Persistent IMAP filtering, debounce batching and full-compare triggering."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .full_compare_launcher import launch_full_compare_task, recover_custom_full_compare_tasks
from .full_compare_task import FullCompareBusyError, create_full_matrix_compare_task
from .imap_mail_client import ImapMailClient, MailHeader
from .mail_trigger_store import load_mail_state, save_mail_state
from .notification_router import scan_custom_notifications


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def mail_matches(header: MailHeader, sender: str, keyword: str) -> bool:
    return bool(header.sender_email and header.sender_email.casefold() == sender.strip().casefold() and keyword in header.subject)


def batch_trigger_id(mails: list[dict[str, Any]]) -> str:
    stable_ids = sorted(str(item["stable_id"]) for item in mails)
    digest = hashlib.sha256("\n".join(stable_ids).encode("utf-8")).hexdigest()
    return f"mail_batch:{digest}"


class MailWatcher:
    def __init__(
        self,
        *,
        client_factory: Callable[[], ImapMailClient] = ImapMailClient,
        task_creator: Callable[..., Any] = create_full_matrix_compare_task,
        launcher: Callable[..., None] = launch_full_compare_task,
        root: Path | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.task_creator = task_creator
        self.launcher = launcher
        self.root = root
        self.sender = os.getenv("MAIL_TRIGGER_SENDER_EMAIL", "fangyue2@mychery.com").strip().lower()
        self.keyword = os.getenv("MAIL_TRIGGER_SUBJECT_KEYWORD", "更新")
        self.delay = int(os.getenv("MAIL_TRIGGER_DELAY_SECONDS", "300"))
        self.debounce = int(os.getenv("MAIL_TRIGGER_DEBOUNCE_SECONDS", "300"))
        self.max_queue_attempts = int(os.getenv("MAIL_TRIGGER_MAX_QUEUE_ATTEMPTS", "20"))

    def poll_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or _utc_now()
        state = load_mail_state(self.root)
        new_count = candidate_count = 0
        client = self.client_factory()
        try:
            client.connect()
            uidvalidity = client.uidvalidity()
            uids = client.list_uids()
            if not state.get("baseline_complete") or state.get("uidvalidity") != uidvalidity:
                initial = os.getenv("MAIL_TRIGGER_INITIAL_BASELINE", "true").strip().lower() == "true"
                if initial or state.get("uidvalidity"):
                    state.update({
                        "uidvalidity": uidvalidity,
                        "baseline_complete": True,
                        "baseline_uids": list(uids),
                        "messages": {},
                        "pending_batch": None,
                        "baseline_created_at": _iso(now),
                        "last_warning": "UIDVALIDITY变化，已安全重建基线" if state.get("uidvalidity") else "",
                    })
                    state["last_poll_at"] = _iso(now)
                    state["last_poll_status"] = "baseline_created"
                    save_mail_state(state, self.root)
                    return {"baseline_created": True, "new_count": 0, "candidate_count": 0, "task_id": ""}
                state.update({"uidvalidity": uidvalidity, "baseline_complete": True, "baseline_uids": [], "messages": {}})

            baseline = set(str(uid) for uid in state.get("baseline_uids", []))
            messages = state.setdefault("messages", {})
            retry_uids = {str(item.get("uid")) for item in messages.values() if item.get("status") == "failed_retryable"}
            targets = [uid for uid in uids if uid not in baseline and f"{uidvalidity}:{uid}" not in messages]
            targets.extend(uid for uid in retry_uids if uid not in targets)
            new_count = len(targets)
            for uid in targets:
                key = f"{uidvalidity}:{uid}"
                try:
                    header = client.fetch_header(uid, uidvalidity)
                except Exception as exc:  # noqa: BLE001
                    previous = messages.get(key, {})
                    messages[key] = {"uid": uid, "uidvalidity": uidvalidity, "status": "failed_retryable", "attempt_count": int(previous.get("attempt_count") or 0) + 1, "last_error": type(exc).__name__, "last_attempt_at": _iso(now)}
                    continue
                stable_id = header.stable_id(client.username, client.folder)
                record = {
                    "uid": uid,
                    "uidvalidity": uidvalidity,
                    "stable_id": stable_id,
                    "message_id": header.message_id,
                    "sender_email": header.sender_email,
                    "subject": header.subject,
                    "date": header.date,
                    "detected_at": _iso(now),
                    "status": "pending" if mail_matches(header, self.sender, self.keyword) else "ignored",
                }
                messages[key] = record
                if record["status"] == "pending":
                    candidate_count += 1
                    self._merge_pending(state, record, now)
            task_id = self._trigger_due_batch(state, now)
            state["last_poll_at"] = _iso(now)
            state["last_poll_status"] = "ok"
            state["last_new_count"] = new_count
            state["last_candidate_count"] = candidate_count
            save_mail_state(state, self.root)
            try:
                scan_custom_notifications()
            except Exception:  # noqa: BLE001 - notification cannot break polling
                pass
            return {"baseline_created": False, "new_count": new_count, "candidate_count": candidate_count, "task_id": task_id}
        finally:
            client.close()

    def _merge_pending(self, state: dict[str, Any], record: dict[str, Any], now: datetime) -> None:
        batch = state.get("pending_batch")
        if not batch or batch.get("status") in {"triggered", "failed"}:
            batch = {"status": "pending", "mails": [], "created_at": _iso(now), "attempt_count": 0}
        known = {item["stable_id"] for item in batch["mails"]}
        if record["stable_id"] not in known:
            batch["mails"].append({key: record[key] for key in ["uid", "uidvalidity", "stable_id", "message_id", "sender_email", "subject", "date"]})
        wait = self.delay if len(batch["mails"]) == 1 else self.debounce
        batch["last_candidate_at"] = _iso(now)
        batch["due_at"] = _iso(now + timedelta(seconds=wait))
        batch["trigger_id"] = batch_trigger_id(batch["mails"])
        state["pending_batch"] = batch

    def _trigger_due_batch(self, state: dict[str, Any], now: datetime) -> str:
        batch = state.get("pending_batch") or {}
        if batch.get("status") not in {"pending", "queued"} or not batch.get("mails"):
            return ""
        due = _parse(str(batch.get("due_at") or ""))
        if not due or now < due:
            return ""
        mails = batch["mails"]
        metadata = {
            "trigger_method": "imap_email",
            "email_sender": self.sender,
            "email_subjects": [item["subject"] for item in mails],
            "email_received_at_list": [item["date"] for item in mails],
            "email_message_ids": [item["message_id"] for item in mails],
            "imap_uids": [item["uid"] for item in mails],
            "mail_batch_size": len(mails),
        }
        try:
            result = self.task_creator(
                trigger_source="email_auto",
                trigger_id=batch["trigger_id"],
                requested_by=self.sender,
                notify_type="feishu_custom_bot",
                notify_target=os.getenv("FEISHU_CUSTOM_BOT_NOTIFY_TARGET", "default_group"),
                trigger_metadata=metadata,
                root=self.root,
            )
        except FullCompareBusyError:
            batch["attempt_count"] = int(batch.get("attempt_count") or 0) + 1
            if batch["attempt_count"] >= self.max_queue_attempts:
                batch["status"] = "failed"
                batch["last_error"] = "等待密集任务超出最大补跑次数"
            else:
                batch["status"] = "queued"
                batch["due_at"] = _iso(now + timedelta(seconds=max(30, int(os.getenv("MAIL_POLL_INTERVAL_SECONDS", "60")))))
            return ""
        self.launcher(result)
        batch.update({"status": "triggered", "task_id": result.task_id, "triggered_at": _iso(now), "last_error": ""})
        stable_ids = {item["stable_id"] for item in mails}
        for record in state.get("messages", {}).values():
            if record.get("stable_id") in stable_ids:
                record.update({"status": "triggered", "task_id": result.task_id})
        return result.task_id

    def run_forever(self) -> None:
        interval = max(10, int(os.getenv("MAIL_POLL_INTERVAL_SECONDS", "60")))
        backoff = 5
        try:
            recover_custom_full_compare_tasks()
            scan_custom_notifications()
        except Exception:  # noqa: BLE001
            pass
        while True:
            try:
                result = self.poll_once()
                print(f"mail poll ok new={result['new_count']} candidates={result['candidate_count']} task_id={result['task_id'] or '-'}", flush=True)
                backoff = 5
                time.sleep(interval)
            except Exception as exc:  # noqa: BLE001
                print(f"mail poll failed type={type(exc).__name__}; retry in {backoff}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
