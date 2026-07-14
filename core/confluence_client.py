"""Confluence Server/Data Center REST client for matrix Excel discovery."""

from __future__ import annotations

import argparse
import ipaddress
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests

from .file_intake import sanitize_filename

EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
EXCEL_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
}


class ConfluenceError(RuntimeError):
    pass


class ConfluenceAuthError(ConfluenceError):
    pass


class ConfluencePermissionError(ConfluenceError):
    pass


class ConfluenceNotFoundError(ConfluenceError):
    pass


class ConfluenceRateLimitError(ConfluenceError):
    pass


class ConfluenceClient:
    def __init__(
        self,
        base_url: str | None = None,
        pat: str | None = None,
        timeout_seconds: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("CONFLUENCE_BASE_URL", "")).rstrip("/")
        self.pat = pat if pat is not None else os.getenv("CONFLUENCE_PAT", "")
        self.timeout = timeout_seconds or int(os.getenv("CONFLUENCE_TIMEOUT_SECONDS", "30"))
        self.allowed_hosts = [host.lower() for host in (_split_env("CONFLUENCE_ALLOWED_HOSTS") or ([urlparse(self.base_url).hostname] if self.base_url else [])) if host]
        self.allowed_space_keys = set(_split_env("CONFLUENCE_ALLOWED_SPACE_KEYS"))
        self.max_pages = int(os.getenv("CONFLUENCE_MAX_PAGES", "500"))
        self.max_attachments = int(os.getenv("CONFLUENCE_MAX_ATTACHMENTS", "500"))
        self.max_file_size = int(os.getenv("CONFLUENCE_MAX_FILE_SIZE_MB", "100")) * 1024 * 1024
        self.parent_include_self = os.getenv("CONFLUENCE_PARENT_INCLUDE_SELF", "false").strip().lower() == "true"
        self.verify = _ssl_verify_value()
        self.session = session or requests.Session()
        if not self.base_url:
            raise ConfluenceError("缺少 CONFLUENCE_BASE_URL")
        if not self.pat:
            raise ConfluenceAuthError("缺少 CONFLUENCE_PAT")
        self.session.headers.update({"Authorization": f"Bearer {self.pat}", "Accept": "application/json"})
        self._validate_allowed_url(self.base_url)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "ConfluenceClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _validate_allowed_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.username or parsed.password:
            raise ConfluenceError("Confluence URL 不允许包含用户名或密码")
        if parsed.scheme not in {"http", "https"}:
            raise ConfluenceError("Confluence URL 仅支持 http/https")
        if _is_ip_literal(host) or host in {"localhost"}:
            raise ConfluenceError(f"禁止直接访问 IP 或本地地址：{host}")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise ConfluenceError(f"Confluence Host 不在白名单中：{host}")

    def _request_once(self, method: str, url: str, *, params: dict[str, Any] | None = None, stream: bool = False) -> requests.Response:
        self._validate_allowed_url(url)
        current_url = url
        current_params = params
        for _ in range(5):
            response = self.session.request(
                method,
                current_url,
                params=current_params,
                timeout=(10, self.timeout),
                verify=self.verify,
                stream=stream,
                allow_redirects=False,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location", "")
                if not location:
                    raise ConfluenceError("Confluence 重定向缺少 Location")
                next_url = urljoin(current_url, location)
                self._validate_allowed_url(next_url)
                current_url = next_url
                current_params = None
                continue
            self._validate_allowed_url(response.url or current_url)
            return response
        raise ConfluenceError("Confluence 重定向次数过多")

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None = None, stream: bool = False) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._request_once(method, url, params=params, stream=stream)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < 2:
                        time.sleep(1 + attempt * 2)
                        continue
                self._raise_for_status(response)
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt * 2)
                    continue
                raise ConfluenceError(f"Confluence 请求失败：{exc}") from exc
        raise ConfluenceError(f"Confluence 请求失败：{last_error}")

    def _api(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request("GET", urljoin(self.base_url + "/", path.lstrip("/")), params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise ConfluenceError("Confluence 返回非 JSON 内容") from exc

    def _raise_for_status(self, response: requests.Response) -> None:
        code = response.status_code
        if code < 400:
            return
        if code == 401:
            raise ConfluenceAuthError("Confluence 鉴权失败：PAT 无效或已过期")
        if code == 403:
            raise ConfluencePermissionError("Confluence 无权限访问该页面或附件")
        if code == 404:
            raise ConfluenceNotFoundError("Confluence 页面或附件不存在")
        if code == 429:
            raise ConfluenceRateLimitError("Confluence 请求过于频繁，请稍后重试")
        if 500 <= code < 600:
            raise ConfluenceError(f"Confluence 服务端错误：HTTP {code}")
        raise ConfluenceError(f"Confluence 请求失败：HTTP {code}")

    def resolve_page_id(self, url: str) -> str:
        self._validate_allowed_url(url)
        parsed = urlparse(url)
        page_id = parse_qs(parsed.query).get("pageId", [""])[0]
        if page_id:
            return page_id
        for part in parsed.path.strip("/").split("/"):
            if part.isdigit() and len(part) >= 4:
                return part
        if "/display/" in parsed.path:
            parts = parsed.path.split("/display/", 1)[1].split("/", 1)
            if len(parts) == 2:
                space_key = unquote(parts[0])
                title = unquote(parts[1]).replace("+", " ")
                if self.allowed_space_keys and space_key not in self.allowed_space_keys:
                    raise ConfluencePermissionError(f"Space 不在允许范围：{space_key}")
                data = self._api("/rest/api/content", {"spaceKey": space_key, "title": title, "type": "page", "limit": 1})
                results = data.get("results") or []
                if results:
                    return str(results[0]["id"])
        if "/x/" in parsed.path:
            response = self._request("GET", url, stream=False)
            final_url = response.url
            if final_url != url:
                return self.resolve_page_id(final_url)
        raise ConfluenceError("无法从 Confluence URL 解析 page_id")

    def get_page(self, page_id: str) -> dict[str, Any]:
        page = self._api(f"/rest/api/content/{page_id}", {"expand": "space"})
        self._check_space(page)
        return page

    def list_child_pages(self, page_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/rest/api/content/{page_id}/child/page", {"expand": "space"}, limit_key="pages")

    def list_descendant_pages(self, page_id: str) -> list[dict[str, Any]]:
        visited: set[str] = set()
        out: list[dict[str, Any]] = []
        queue = list(self.list_child_pages(page_id))
        while queue:
            page = queue.pop(0)
            pid = str(page.get("id", ""))
            if not pid or pid in visited:
                continue
            visited.add(pid)
            self._check_space(page)
            out.append(page)
            if len(out) > self.max_pages:
                raise ConfluenceError(f"超过最大页面遍历数量限制：{self.max_pages}")
            queue.extend(self.list_child_pages(pid))
        return out

    def list_attachments(self, page_id: str) -> list[dict[str, Any]]:
        return self._paged(f"/rest/api/content/{page_id}/child/attachment", {"expand": "metadata"}, limit_key="attachments")

    def discover_excel_attachments(self, page_id: str, mode: str = "current_page", source_url: str = "") -> list[dict[str, Any]]:
        pages = [self.get_page(page_id)] if mode == "current_page" or self.parent_include_self else []
        if mode == "children_recursive":
            pages.extend(self.list_descendant_pages(page_id))
        seen_attachments: set[str] = set()
        result: list[dict[str, Any]] = []
        for page in pages:
            pid = str(page.get("id", ""))
            title = page.get("title", "")
            for att in self.list_attachments(pid):
                if len(result) >= self.max_attachments:
                    raise ConfluenceError(f"超过最大附件数量限制：{self.max_attachments}")
                if not _is_excel_attachment(att, self.max_file_size):
                    continue
                aid = str(att.get("id", ""))
                if aid and aid in seen_attachments:
                    continue
                seen_attachments.add(aid)
                result.append({
                    "attachment_id": aid,
                    "page_id": pid,
                    "page_title": title,
                    "file_name": att.get("title", ""),
                    "media_type": att.get("metadata", {}).get("mediaType", ""),
                    "file_size": int(att.get("extensions", {}).get("fileSize") or att.get("metadata", {}).get("fileSize") or 0),
                    "download_link": att.get("_links", {}).get("download", ""),
                    "source_url": source_url,
                })
        return result

    def download_attachment(self, attachment: dict[str, Any], target_dir: Path) -> Path:
        download_link = attachment.get("download_link") or attachment.get("_links", {}).get("download")
        if not download_link:
            raise ConfluenceError(f"附件缺少下载链接：{attachment.get('file_name', '')}")
        url = urljoin(self.base_url + "/", str(download_link).lstrip("/"))
        file_name = sanitize_filename(attachment.get("file_name") or "attachment.xlsx")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / file_name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            aid = str(attachment.get("attachment_id") or attachment.get("page_id") or "dup")[:12]
            target = target_dir / f"{stem}_{aid}{suffix}"
            index = 1
            while target.exists():
                target = target_dir / f"{stem}_{aid}_{index}{suffix}"
                index += 1
        response = self._request("GET", url, stream=True)
        total = int(response.headers.get("Content-Length") or 0)
        if total and total > self.max_file_size:
            raise ConfluenceError(f"附件超过大小限制：{file_name}")
        written = 0
        with target.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > self.max_file_size:
                    target.unlink(missing_ok=True)
                    raise ConfluenceError(f"附件超过大小限制：{file_name}")
                fh.write(chunk)
        return target

    def test_connection(self, test_url: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"base_url": self.base_url, "authenticated": False}
        page_id = self.resolve_page_id(test_url) if test_url else None
        if page_id:
            page = self.get_page(page_id)
            children = self.list_child_pages(page_id)
            attachments = self.discover_excel_attachments(page_id, "current_page", test_url or "")
            result.update({"authenticated": True, "page_id": page_id, "page_title": page.get("title", ""), "child_page_count": len(children), "excel_attachment_count": len(attachments)})
            return result
        data = self._api("/rest/api/content", {"limit": 1})
        result["authenticated"] = isinstance(data, dict)
        return result

    def _paged(self, path: str, params: dict[str, Any] | None = None, limit_key: str = "items") -> list[dict[str, Any]]:
        params = dict(params or {})
        start = 0
        limit = 50
        items: list[dict[str, Any]] = []
        while True:
            page_params = {**params, "start": start, "limit": limit}
            data = self._api(path, page_params)
            results = data.get("results") or []
            for item in results:
                if limit_key == "pages":
                    self._check_space(item)
            items.extend(results)
            size = int(data.get("size") or len(results))
            if size < limit or not results:
                break
            start += size
        return items

    def _check_space(self, page: dict[str, Any]) -> None:
        if not self.allowed_space_keys:
            return
        space_key = page.get("space", {}).get("key") or page.get("_expandable", {}).get("space")
        if space_key and space_key not in self.allowed_space_keys:
            raise ConfluencePermissionError(f"Space 不在允许范围：{space_key}")


def _split_env(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


def _ssl_verify_value() -> bool | str:
    verify_ssl = os.getenv("CONFLUENCE_VERIFY_SSL", "true").strip().lower() != "false"
    ca_bundle = os.getenv("CONFLUENCE_CA_BUNDLE", "").strip()
    return ca_bundle if ca_bundle else verify_ssl


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address((host or "").strip("[]"))
        return True
    except ValueError:
        return False


def _is_excel_attachment(att: dict[str, Any], max_file_size: int) -> bool:
    title = str(att.get("title") or "")
    suffix = Path(title).suffix.lower()
    if suffix not in EXCEL_EXTENSIONS:
        return False
    media_type = att.get("metadata", {}).get("mediaType", "")
    if media_type and media_type not in EXCEL_MEDIA_TYPES and "spreadsheet" not in media_type.lower() and "excel" not in media_type.lower():
        return False
    size = int(att.get("extensions", {}).get("fileSize") or att.get("metadata", {}).get("fileSize") or 0)
    if size and size > max_file_size:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Confluence connection without printing PAT")
    parser.add_argument("--test-url", default="")
    args = parser.parse_args()
    with ConfluenceClient() as client:
        data = client.test_connection(args.test_url or None)
    for key, value in data.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
