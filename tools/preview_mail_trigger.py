from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mail_trigger_store import load_mail_state


def main() -> int:
    state = load_mail_state()
    safe = {
        "uidvalidity": state.get("uidvalidity"),
        "baseline_complete": state.get("baseline_complete"),
        "baseline_count": len(state.get("baseline_uids", [])),
        "message_status_counts": {},
        "pending_batch": state.get("pending_batch"),
        "last_poll_at": state.get("last_poll_at"),
        "last_poll_status": state.get("last_poll_status"),
        "last_warning": state.get("last_warning"),
    }
    for item in state.get("messages", {}).values():
        status = item.get("status", "unknown")
        safe["message_status_counts"][status] = safe["message_status_counts"].get(status, 0) + 1
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
