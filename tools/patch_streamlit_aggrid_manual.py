"""Apply the project-specific Manual Update fix to streamlit-aggrid 1.1.9."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


BUNDLE_RELATIVE_PATH = Path(
    "st_aggrid/frontend/build/static/js/main.db06ce24.js"
)
ORIGINAL_SHA256 = "35b1e81df4820c9ec69fe96c95473cd28af2add8b84d997b0b1093b6b3538d10"
PATCHED_SHA256 = "10c9cadd87182fc29379074f84ca74e23b18e6ce6cd8e58e62ab490e932830b7"

ORIGINAL_HANDLER = (
    'onManualUpdateClick:()=>{this.state.debug&&'
    'console.log("Manual update triggered")}'
).encode("utf-8")

PATCHED_HANDLER = (
    'onManualUpdateClick:()=>{var e;null===(e=this.state.api)||'
    'void 0===e||e.stopEditing(),this.state.debug&&'
    'console.log("Manual update triggered"),this.returnGridValue('
    '{type:"manualUpdate",api:this.state.api},"manualUpdate")}'
).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def apply_exact_patch(content: bytes) -> bytes:
    """Replace exactly one known Manual handler and reject ambiguous inputs."""

    match_count = content.count(ORIGINAL_HANDLER)
    if match_count != 1:
        raise RuntimeError(
            "Manual handler match count must be exactly 1; "
            f"found {match_count}"
        )
    if content.count(PATCHED_HANDLER):
        raise RuntimeError("Patched Manual handler is already present unexpectedly")
    return content.replace(ORIGINAL_HANDLER, PATCHED_HANDLER, 1)


def patch_bundle(source_dir: Path) -> dict[str, str | bool]:
    source_dir = source_dir.resolve()
    bundle_path = source_dir / BUNDLE_RELATIVE_PATH
    if not bundle_path.is_file():
        raise FileNotFoundError(f"Expected bundle not found: {bundle_path}")

    original = bundle_path.read_bytes()
    before_sha256 = sha256_bytes(original)

    if before_sha256 == PATCHED_SHA256:
        if (
            original.count(PATCHED_HANDLER) != 1
            or original.count(ORIGINAL_HANDLER) != 0
        ):
            raise RuntimeError(
                "Bundle has patched hash but handler contents are inconsistent"
            )
        return {
            "bundle": str(bundle_path),
            "before_sha256": before_sha256,
            "after_sha256": before_sha256,
            "already_patched": True,
        }

    if before_sha256 != ORIGINAL_SHA256:
        raise RuntimeError(
            "Refusing to patch unexpected bundle hash: "
            f"{before_sha256}; expected {ORIGINAL_SHA256}"
        )

    patched = apply_exact_patch(original)
    after_sha256 = sha256_bytes(patched)
    if after_sha256 != PATCHED_SHA256:
        raise RuntimeError(
            "Patched bundle hash does not match the reviewed artifact: "
            f"{after_sha256}; expected {PATCHED_SHA256}"
        )

    temporary_path = bundle_path.with_suffix(bundle_path.suffix + ".manual.tmp")
    temporary_path.write_bytes(patched)
    temporary_path.replace(bundle_path)

    return {
        "bundle": str(bundle_path),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "already_patched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch the exact streamlit-aggrid 1.1.9 frontend bundle"
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Path to the extracted streamlit-aggrid 1.1.9 source directory",
    )
    args = parser.parse_args()
    print(json.dumps(patch_bundle(args.source_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
