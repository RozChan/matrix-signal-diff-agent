from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_service


class FinishedEventProcess:
    def __init__(self, return_code: int) -> None:
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.stdin = io.StringIO()
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code


class FinishedEventClient:
    def __init__(self, return_code: int) -> None:
        self.process = FinishedEventProcess(return_code)

    def open_event_consumer(self) -> FinishedEventProcess:
        return self.process


def test_event_consumer_clean_cli_exit_is_reported_as_service_failure() -> None:
    assert bot_service.consume_events(FinishedEventClient(0)) == 1


def test_event_consumer_preserves_nonzero_cli_exit_code() -> None:
    assert bot_service.consume_events(FinishedEventClient(7)) == 7
