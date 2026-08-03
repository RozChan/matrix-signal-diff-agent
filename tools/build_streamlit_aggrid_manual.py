"""Build the reproducible project-local streamlit-aggrid Manual wheel."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.patch_streamlit_aggrid_manual import (
    BUNDLE_RELATIVE_PATH,
    ORIGINAL_SHA256,
    patch_bundle,
    sha256_bytes,
)


UPSTREAM_SOURCE = (
    PROJECT_ROOT / "vendor" / "streamlit-aggrid-manual" / "upstream"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "vendor" / "wheels"
UPSTREAM_VERSION = 'version = "1.1.9"'
LOCAL_VERSION = 'version = "1.1.9+manual.3"'


def prepare_source(upstream: Path, destination: Path) -> None:
    bundle = upstream / BUNDLE_RELATIVE_PATH
    if not bundle.is_file():
        raise FileNotFoundError(f"Upstream bundle not found: {bundle}")
    actual_hash = sha256_bytes(bundle.read_bytes())
    if actual_hash != ORIGINAL_SHA256:
        raise RuntimeError(
            f"Upstream bundle hash is {actual_hash}, expected {ORIGINAL_SHA256}"
        )

    shutil.copytree(upstream, destination)
    patch_bundle(destination)

    pyproject = destination / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match_count = content.count(UPSTREAM_VERSION)
    if match_count != 1:
        raise RuntimeError(
            "Expected exactly one upstream version declaration; "
            f"found {match_count}"
        )
    pyproject.write_text(
        content.replace(UPSTREAM_VERSION, LOCAL_VERSION, 1),
        encoding="utf-8",
        newline="\n",
    )


def build_wheel(upstream: Path, output: Path) -> None:
    upstream = upstream.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="streamlit_aggrid_manual_build_"
    ) as temporary:
        source = Path(temporary) / "streamlit_aggrid-1.1.9+manual.3"
        prepare_source(upstream, source)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(output),
                str(source),
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=UPSTREAM_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_wheel(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
