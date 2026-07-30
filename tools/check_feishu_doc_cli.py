"""Print the installed lark-cli document command help for workstation verification."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.feishu_doc_service import FeishuDocumentError, check_lark_cli_environment


def main() -> int:
    try:
        environment = check_lark_cli_environment()
    except FeishuDocumentError as exc:
        print(f"{exc.error_type}: {exc}", file=sys.stderr)
        return 2
    cli_path = environment["cli_path"]
    for args in (("docs", "+create", "--help"), ("docs", "+media-insert", "--help")):
        print(f"\n=== {' '.join(args)} ===")
        result = subprocess.run(
            [cli_path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=30,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
