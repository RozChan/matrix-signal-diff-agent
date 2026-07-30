from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_streamlit import REPO_ROOT, parse_review_base_url, streamlit_command


def test_review_base_url_drives_streamlit_host_and_port() -> None:
    host, port, normalized = parse_review_base_url("http://10.105.194.152:8501/")
    assert (host, port, normalized) == ("10.105.194.152", 8501, "http://10.105.194.152:8501")
    command = streamlit_command(host, port)
    assert command[command.index("--browser.serverAddress") + 1] == "10.105.194.152"
    assert command[command.index("--server.port") + 1] == "8501"
    assert command[command.index("--server.address") + 1] == "0.0.0.0"
    assert str(REPO_ROOT / "app.py") in command


@pytest.mark.parametrize(
    "value",
    ["", "10.105.194.152:8501", "ftp://host:8501", "http://host:bad", "http://host:8501/review", "http://host:8501?x=1"],
)
def test_invalid_review_base_url_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_review_base_url(value)


def test_default_scheme_ports_are_supported() -> None:
    assert parse_review_base_url("http://host")[:2] == ("host", 80)
    assert parse_review_base_url("https://host")[:2] == ("host", 443)
