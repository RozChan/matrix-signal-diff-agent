"""Pure Confluence page classification and latest-version selection helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable


_VERSION_RE = re.compile(r"(?i)(?:^|[-_\s])v\s*(\d+(?:[._]\d+)+)(?=$|[_\-\s（(])")
_ONLY_VERSION_RE = re.compile(r"(?i)^\s*v\s*\d+(?:[._]\d+)+\s*[_-]?\s*(?:[（(][^）)]*[）)])?\s*$")
_STRUCTURAL_TITLES = {"版本", "版本记录", "当前版本", "历史版本", "current", "versions", "history"}


@dataclass(frozen=True)
class VersionInfo:
    raw_version: str
    normalized_version: str
    version_tuple: tuple[int, ...]
    parse_confidence: str


def parse_page_version(title: str) -> VersionInfo | None:
    """Parse a page version as integers (never as a string or float)."""

    cleaned = str(title or "").strip()
    match = _VERSION_RE.search(f" {cleaned}")
    if not match:
        # Pure version titles do not have the synthetic leading separator in all cases.
        match = re.search(r"(?i)v\s*(\d+(?:[._]\d+)+)", cleaned)
    if not match:
        return None
    numbers = tuple(int(part) for part in re.split(r"[._]", match.group(1)))
    raw = match.group(0).strip(" -_")
    return VersionInfo(raw, "V" + ".".join(str(part) for part in numbers), numbers, "high")


def classify_page(title: str, *, has_version_children: bool = False) -> str:
    text = str(title or "").strip()
    version = parse_page_version(text)
    if _ONLY_VERSION_RE.match(text):
        return "version_page"
    if version:
        return "direct_versioned_module"
    if has_version_children:
        return "module_container"
    return "unclassified_page"


def _updated_sort_value(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def select_latest_version_pages(page_tree: Iterable[dict[str, Any]], *, strict: bool = True) -> dict[str, Any]:
    """Select one newest version page per nearest module container.

    Nodes use ``parent_id``/``ancestor_ids`` relationships. Direct versioned
    module pages form their own module unless they live below a container.
    Ambiguous equal maximum versions are warnings and are not selected in
    strict mode.
    """

    nodes = {str(node.get("page_id") or node.get("id") or ""): dict(node) for node in page_tree}
    nodes.pop("", None)
    children: dict[str, list[str]] = {pid: [] for pid in nodes}
    for pid, node in nodes.items():
        parent_id = str(node.get("parent_id") or "")
        if parent_id in children:
            children[parent_id].append(pid)
    classifications: dict[str, str] = {}
    for pid, node in nodes.items():
        pending = list(children.get(pid, []))
        seen_descendants: set[str] = set()
        has_version_children = False
        while pending:
            child = pending.pop()
            if child in seen_descendants:
                continue
            seen_descendants.add(child)
            if classify_page(str(nodes[child].get("title") or "")) in {"version_page", "direct_versioned_module"}:
                has_version_children = True
                break
            pending.extend(children.get(child, []))
        if not node.get("parent_id") or str(node.get("title") or "").strip().casefold() in _STRUCTURAL_TITLES:
            has_version_children = False
        classifications[pid] = classify_page(str(node.get("title") or ""), has_version_children=has_version_children)

    containers = {pid for pid, kind in classifications.items() if kind == "module_container"}
    groups: dict[str, list[tuple[dict[str, Any], VersionInfo]]] = {}
    warnings: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    for pid, node in nodes.items():
        kind = classifications[pid]
        if kind not in {"version_page", "direct_versioned_module"}:
            if kind == "unclassified_page" and node.get("parent_id"):
                unclassified.append({"page_id": pid, "title": node.get("title", ""), "warning": "无法识别页面分类或版本"})
            continue
        version = parse_page_version(str(node.get("title") or ""))
        if not version:
            continue
        ancestors = [str(value) for value in node.get("ancestor_ids", [])]
        nearest = next((ancestor for ancestor in reversed(ancestors) if ancestor in containers), "")
        module_key = nearest or pid
        groups.setdefault(module_key, []).append((node, version))

    selections: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for module_key, candidates in groups.items():
        best_tuple = max(version.version_tuple for _, version in candidates)
        best = [(node, version) for node, version in candidates if version.version_tuple == best_tuple]
        best.sort(key=lambda pair: _updated_sort_value(pair[0].get("updated_at")), reverse=True)
        module_node = nodes.get(module_key, best[0][0])
        warning_messages: list[str] = []
        selected: tuple[dict[str, Any], VersionInfo] | None = best[0]
        if len(best) > 1 and _updated_sort_value(best[0][0].get("updated_at")) > _updated_sort_value(best[1][0].get("updated_at")):
            selected = best[0]
        elif len(best) > 1:
            warning_messages.append("同一最大版本对应多个页面")
            if strict:
                selected = None
        skipped = []
        for node, version in candidates:
            pid = str(node.get("page_id") or node.get("id"))
            if selected and pid == str(selected[0].get("page_id") or selected[0].get("id")):
                continue
            skipped.append({"page_id": pid, "page_title": node.get("title", ""), "version": version.normalized_version, "reason": "历史版本" if version.version_tuple < best_tuple else "版本歧义"})
        record = {
            "module_key": module_key,
            "module_title": module_node.get("title", ""),
            "versions_found": [asdict(version) for _, version in candidates],
            "selected_page_id": "",
            "selected_page_title": "",
            "selected_version_raw": "",
            "selected_version_normalized": "",
            "selected_version_tuple": [],
            "selection_reason": "strict_ambiguity" if selected is None else "highest_numeric_version",
            "selected_page_updated_at": "",
            "selected_attachments": [],
            "skipped_pages": skipped,
            "warnings": warning_messages,
        }
        if selected:
            node, version = selected
            selected_id = str(node.get("page_id") or node.get("id"))
            selected_ids.add(selected_id)
            record.update({
                "selected_page_id": selected_id,
                "selected_page_title": node.get("title", ""),
                "selected_version_raw": version.raw_version,
                "selected_version_normalized": version.normalized_version,
                "selected_version_tuple": list(version.version_tuple),
                "selected_page_updated_at": node.get("updated_at", ""),
            })
        selections.append(record)
        if warning_messages:
            warnings.append({"module_key": module_key, "module_title": module_node.get("title", ""), "warnings": warning_messages})
    return {
        "selections": selections,
        "selected_page_ids": sorted(selected_ids),
        "warnings": warnings,
        "unclassified_pages": unclassified,
        "strict_blocked": bool(strict and warnings),
    }
