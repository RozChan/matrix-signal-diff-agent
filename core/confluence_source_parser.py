"""Deterministic parsing of Confluence source URLs from Feishu messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Mode = Literal["current_page", "children_recursive"]
Version = Literal["4.0", "5.1"]

URL_RE = re.compile(r"https?://[^\s，,。；;）)】>\]]+")


@dataclass(frozen=True)
class ParsedConfluenceSource:
    version: str
    mode: str
    url: str


@dataclass(frozen=True)
class ParseResult:
    sources: list[ParsedConfluenceSource]
    unresolved_urls: list[str]


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".。；;，,")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def detect_version(text: str) -> str:
    normalized = (text or "").upper().replace(" ", "")
    if "EEA4.0" in normalized or "4.0" in normalized:
        return "4.0"
    if "EEA5.1" in normalized or "5.1" in normalized:
        return "5.1"
    return ""


def detect_mode(text: str) -> str:
    raw = text or ""
    if any(keyword in raw for keyword in ["父页面", "子页面", "下面所有页面", "所有子页面", "递归"]):
        return "children_recursive"
    if any(keyword in raw for keyword in ["页面", "网址", "链接"]):
        return "current_page"
    return ""


def parse_confluence_sources(text: str) -> ParseResult:
    sources: list[ParsedConfluenceSource] = []
    unresolved: list[str] = []
    current_version = ""
    current_mode = "current_page"
    for raw_line in (text or "").splitlines() or [text or ""]:
        line = raw_line.strip()
        if not line:
            continue
        line_version = detect_version(line) or current_version
        line_mode = detect_mode(line) or current_mode
        if detect_version(line):
            current_version = detect_version(line)
        if any(keyword in line for keyword in ["父页面", "子页面", "下面所有页面", "所有子页面", "递归", "页面", "网址", "链接"]):
            current_mode = line_mode
        for url in extract_urls(line):
            if line_version in {"4.0", "5.1"}:
                sources.append(ParsedConfluenceSource(line_version, line_mode, url))
            else:
                unresolved.append(url)
    return ParseResult(sources=sources, unresolved_urls=unresolved)
