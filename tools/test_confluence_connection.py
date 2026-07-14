"""Local Confluence connection test helper. Does not print CONFLUENCE_PAT."""

from __future__ import annotations

import argparse

from core.confluence_client import ConfluenceClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Confluence connection and URL parsing")
    parser.add_argument("--test-url", required=True)
    args = parser.parse_args()
    with ConfluenceClient() as client:
        result = client.test_connection(args.test_url)
    print(f"Base URL: {result.get('base_url')}")
    print(f"Authenticated: {result.get('authenticated')}")
    print(f"Page ID: {result.get('page_id', '')}")
    print(f"Page Title: {result.get('page_title', '')}")
    print(f"Child Page Count: {result.get('child_page_count', 0)}")
    print(f"Excel Attachment Count: {result.get('excel_attachment_count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
