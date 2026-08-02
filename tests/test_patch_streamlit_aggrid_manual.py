from __future__ import annotations

from pathlib import Path

import pytest

from tools.patch_streamlit_aggrid_manual import (
    BUNDLE_RELATIVE_PATH,
    ORIGINAL_HANDLER,
    ORIGINAL_SHA256,
    PATCHED_HANDLER,
    PATCHED_SHA256,
    PATCHED_TOOLBAR,
    TOOLBAR_END,
    TOOLBAR_START,
    apply_exact_patch,
    find_original_toolbar,
    patch_bundle,
    sha256_bytes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_BUNDLE = (
    PROJECT_ROOT
    / "vendor"
    / "streamlit-aggrid-manual"
    / "upstream"
    / BUNDLE_RELATIVE_PATH
)


def copied_source(tmp_path: Path) -> Path:
    bundle = tmp_path / BUNDLE_RELATIVE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(UPSTREAM_BUNDLE.read_bytes())
    return tmp_path


def test_upstream_bundle_has_reviewed_original_hash() -> None:
    assert sha256_bytes(UPSTREAM_BUNDLE.read_bytes()) == ORIGINAL_SHA256


def test_patch_requires_exactly_one_handler_match() -> None:
    with pytest.raises(RuntimeError, match="found 0"):
        apply_exact_patch(b"no handler")
    with pytest.raises(RuntimeError, match="found 2"):
        apply_exact_patch(ORIGINAL_HANDLER + b"x" + ORIGINAL_HANDLER)


def test_patch_requires_exactly_one_toolbar_boundary() -> None:
    content = UPSTREAM_BUNDLE.read_bytes()
    with pytest.raises(RuntimeError, match="start=0, end=1"):
        find_original_toolbar(content.replace(TOOLBAR_START, b"missing", 1))
    with pytest.raises(RuntimeError, match="start=2, end=1"):
        find_original_toolbar(TOOLBAR_START + content)


def test_patch_rejects_changed_toolbar_component() -> None:
    content = UPSTREAM_BUNDLE.read_bytes()
    start, end, toolbar = find_original_toolbar(content)
    assert content[end:].startswith(TOOLBAR_END)
    changed = (
        content[:start]
        + toolbar.replace(b"Quick Search", b"Other Search")
        + content[end:]
    )
    with pytest.raises(RuntimeError, match="toolbar hash"):
        find_original_toolbar(changed)


def test_patch_produces_reviewed_hash(tmp_path: Path) -> None:
    source = copied_source(tmp_path)
    result = patch_bundle(source)
    patched = (source / BUNDLE_RELATIVE_PATH).read_bytes()
    assert result["before_sha256"] == ORIGINAL_SHA256
    assert result["after_sha256"] == PATCHED_SHA256
    assert patched.count(ORIGINAL_HANDLER) == 0
    assert patched.count(PATCHED_HANDLER) == 1
    assert patched.count(PATCHED_TOOLBAR) == 1
    assert patched.count("保存修改".encode("utf-8")) == 2
    assert b"Collapse Toolbar" not in patched
    assert b"Toggle Fullscreen View" not in patched
    assert b"Download as CSV" not in patched
    assert b"Quick Search" not in patched


def test_patch_is_idempotent_and_does_not_patch_twice(tmp_path: Path) -> None:
    source = copied_source(tmp_path)
    patch_bundle(source)
    result = patch_bundle(source)
    assert result["already_patched"] is True
    assert result["before_sha256"] == PATCHED_SHA256
    assert result["after_sha256"] == PATCHED_SHA256


def test_patch_rejects_unreviewed_bundle_hash(tmp_path: Path) -> None:
    source = copied_source(tmp_path)
    bundle = source / BUNDLE_RELATIVE_PATH
    bundle.write_bytes(bundle.read_bytes() + b"unexpected")
    with pytest.raises(RuntimeError, match="unexpected bundle hash"):
        patch_bundle(source)
