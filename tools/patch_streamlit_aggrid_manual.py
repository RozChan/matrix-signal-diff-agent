"""Apply the project-specific Manual Update UI fix to streamlit-aggrid 1.1.9."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


BUNDLE_RELATIVE_PATH = Path(
    "st_aggrid/frontend/build/static/js/main.db06ce24.js"
)
ORIGINAL_SHA256 = "35b1e81df4820c9ec69fe96c95473cd28af2add8b84d997b0b1093b6b3538d10"
PATCHED_SHA256 = "986a070501f6bc75b23d019876a7d654e086590742ffae267d1ad7ab2ff8481d"

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

TOOLBAR_START = b"const Hje="
TOOLBAR_END = b";function Wje"
ORIGINAL_TOOLBAR_SHA256 = (
    "1c8b171ca204149e110904e37a585afece94ee831fbe60c98e5fbf3bfb3c23c6"
)

PATCHED_TOOLBAR = (
    'const Hje=e=>{let{enabled:t,onManualUpdateClick:i,'
    'showManualUpdateButton:n=!1}=e;return t&&n?(0,_je.jsx)("div",'
    '{className:"grid-toolbar",style:{top:10,right:10,left:"auto",'
    'opacity:1,visibility:"visible",cursor:"default"},children:(0,_je.jsx)'
    '("button",{className:"toolbar-button update-button",onClick:i,'
    'title:"保存修改","aria-label":"保存修改",style:{backgroundColor:'
    '"#d32f2f",color:"#fff"},children:(0,_je.jsx)("svg",{xmlns:'
    '"http://www.w3.org/2000/svg",viewBox:"0 0 24 24",width:"16",'
    'height:"16",fill:"currentColor","aria-hidden":"true",children:'
    '(0,_je.jsx)("path",{d:"M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 '
    '2 2h14a2 2 0 0 0 2-2V7l-4-4zM7 5h8v4H7V5zm5 14a3 3 0 1 1 '
    '0-6 3 3 0 0 1 0 6z"})})})}):null}'
).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def find_original_toolbar(content: bytes) -> tuple[int, int, bytes]:
    """Locate and verify the exact compiled GridToolBar component."""

    start_count = content.count(TOOLBAR_START)
    end_count = content.count(TOOLBAR_END)
    if start_count != 1 or end_count != 1:
        raise RuntimeError(
            "Toolbar boundary match counts must both be exactly 1; "
            f"found start={start_count}, end={end_count}"
        )

    start = content.index(TOOLBAR_START)
    end = content.index(TOOLBAR_END, start)
    toolbar = content[start:end]
    toolbar_sha256 = sha256_bytes(toolbar)
    if toolbar_sha256 != ORIGINAL_TOOLBAR_SHA256:
        raise RuntimeError(
            "Compiled toolbar hash does not match the reviewed component: "
            f"{toolbar_sha256}; expected {ORIGINAL_TOOLBAR_SHA256}"
        )
    return start, end, toolbar


def apply_exact_patch(content: bytes) -> bytes:
    """Replace the exact toolbar and Manual handler; reject ambiguous inputs."""

    match_count = content.count(ORIGINAL_HANDLER)
    if match_count != 1:
        raise RuntimeError(
            "Manual handler match count must be exactly 1; "
            f"found {match_count}"
        )
    if content.count(PATCHED_HANDLER):
        raise RuntimeError("Patched Manual handler is already present unexpectedly")

    toolbar_start, toolbar_end, _ = find_original_toolbar(content)
    if content.count(PATCHED_TOOLBAR):
        raise RuntimeError("Patched toolbar is already present unexpectedly")

    with_toolbar = (
        content[:toolbar_start] + PATCHED_TOOLBAR + content[toolbar_end:]
    )
    return with_toolbar.replace(ORIGINAL_HANDLER, PATCHED_HANDLER, 1)


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
            or original.count(PATCHED_TOOLBAR) != 1
            or original.count(TOOLBAR_START) != 1
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
