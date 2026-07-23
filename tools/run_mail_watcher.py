from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mail_trigger_store import load_mail_state
from core.mail_watcher import MailWatcher


def main() -> int:
    if os.getenv("MAIL_WATCHER_ENABLED", "false").strip().lower() != "true":
        print("MAIL_WATCHER_ENABLED=false，邮件监听未启用。", file=sys.stderr)
        return 2
    if os.getenv("MAIL_PROVIDER", "imap").strip().lower() != "imap":
        print("MAIL_PROVIDER当前仅支持imap。", file=sys.stderr)
        return 2
    state = load_mail_state()
    print(
        "mail watcher start "
        f"mailbox={os.getenv('MAIL_IMAP_USERNAME', '')} folder={os.getenv('MAIL_IMAP_FOLDER', 'INBOX')} "
        f"interval={os.getenv('MAIL_POLL_INTERVAL_SECONDS', '60')}s sender={os.getenv('MAIL_TRIGGER_SENDER_EMAIL', '')} "
        f"keyword={os.getenv('MAIL_TRIGGER_SUBJECT_KEYWORD', '更新')} baseline={state.get('baseline_complete')} "
        f"pending={1 if state.get('pending_batch') and state['pending_batch'].get('status') in {'pending', 'queued'} else 0}",
        flush=True,
    )
    MailWatcher().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
