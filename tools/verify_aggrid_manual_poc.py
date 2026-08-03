"""Run repeatable Chrome/Edge browser acceptance tests for the Manual POC."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import FrameLocator, Page, sync_playwright


CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

CONFIRM = "人工确认"
SIGNAL = "信号名"
PENDING = "🔴 待选择（点击此处审核）"
SAME = "🟢 相同"
DIFFERENT = "🟢 不同"


def result_payload(page: Page) -> dict[str, Any]:
    code = page.locator("code").filter(has_text="POC_RESULT_JSON=")
    text = code.inner_text()
    return json.loads(text.split("POC_RESULT_JSON=", 1)[1])


def wait_for_submit(page: Page, minimum_count: int) -> dict[str, Any]:
    deadline = time.time() + 20
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = result_payload(page)
        if int(last.get("submit_count") or 0) >= minimum_count:
            return last
        time.sleep(0.2)
    raise AssertionError(
        f"Manual submit count did not reach {minimum_count}; last={last}"
    )


def review_cell(frame: FrameLocator, row_id: str):
    return frame.locator(
        ".ag-pinned-right-cols-container "
        f'.ag-row[row-id="{row_id}"] '
        f'.ag-cell[col-id="{CONFIRM}"]'
    )


def choose_review(frame: FrameLocator, row_id: str, value: str) -> None:
    cell = review_cell(frame, row_id)
    cell.wait_for(state="visible", timeout=10_000)
    cell.dblclick()
    combo = cell.locator('[role="combobox"]')
    combo.click()
    option = frame.get_by_role("option", name=value, exact=True)
    option.click()
    if cell.inner_text() != value:
        raise AssertionError(f"{row_id} did not display selected value {value}")


def click_manual(frame: FrameLocator) -> None:
    manual = frame.locator('button[title="保存修改"][aria-label="保存修改"]')
    manual.wait_for(state="visible", timeout=5_000)
    manual.click()


def row_value(payload: dict[str, Any], row_id: str) -> str:
    row = next(row for row in payload["rows"] if row["row_id"] == row_id)
    return str(row[CONFIRM])


def run_browser(browser_type, executable: Path, url: str) -> dict[str, Any]:
    browser = browser_type.launch(executable_path=str(executable), headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto(url, wait_until="domcontentloaded")

    frame = page.frame_locator("iframe")
    frame.locator(
        '.ag-pinned-right-cols-container .ag-row[row-id="poc-row-01"]'
    ).wait_for(state="visible", timeout=30_000)

    evidence: dict[str, Any] = {
        "browser_executable": str(executable),
        "toolbar": {},
        "page_1": {},
        "page_2": {},
        "sorting": {},
        "filtering": {},
        "page_size": {},
        "multirow": {},
        "change_back": {},
        "consecutive_submit": {},
    }

    # The manual.2 toolbar is always visible and contains only the save button.
    toolbar = frame.locator(".grid-toolbar")
    toolbar.wait_for(state="visible", timeout=5_000)
    save_button = toolbar.locator(
        'button[title="保存修改"][aria-label="保存修改"]'
    )
    if toolbar.locator("button").count() != 1 or save_button.count() != 1:
        raise AssertionError("Toolbar must contain exactly one save button")
    old_toolbar_controls = frame.locator(
        '[title="Collapse Toolbar"], [title="Expand Toolbar"], '
        '[title="Drag Toolbar"], [title="Toggle Fullscreen View"], '
        '[title="Download as CSV"], [title="Quick Search"], '
        'input[placeholder="Search..."]'
    )
    if old_toolbar_controls.count() != 0:
        raise AssertionError("Legacy toolbar controls are still rendered")
    save_style = save_button.evaluate(
        "element => { const style = getComputedStyle(element); "
        "return { backgroundColor: style.backgroundColor, color: style.color }; }"
    )
    if save_style["backgroundColor"] != "rgb(211, 47, 47)":
        raise AssertionError(f"Unexpected save background: {save_style}")
    if save_style["color"] != "rgb(255, 255, 255)":
        raise AssertionError(f"Unexpected save icon color: {save_style}")
    if save_button.locator('svg[aria-hidden="true"] path').count() != 1:
        raise AssertionError("White save icon was not rendered")
    evidence["toolbar"] = {
        "visible_without_hover": save_button.is_visible(),
        "button_count": toolbar.locator("button").count(),
        "title": save_button.get_attribute("title"),
        "aria_label": save_button.get_attribute("aria-label"),
        "background_color": save_style["backgroundColor"],
        "icon_color": save_style["color"],
        "legacy_control_count": old_toolbar_controls.count(),
    }

    # Static grid behavior and first-page editing.
    page_1_text = frame.locator(".ag-paging-panel").inner_text()
    row_id_header_count = frame.locator(
        '.ag-header-cell[col-id="row_id"]'
    ).count()
    pinned_header = frame.locator(
        f'.ag-pinned-right-header .ag-header-cell[col-id="{CONFIRM}"]'
    )
    multiline_text = frame.locator(
        '.ag-center-cols-container .ag-row[row-id="poc-row-01"] '
        '.ag-cell[col-id="EEA4.0字段值"]'
    ).inner_text()
    choose_review(frame, "poc-row-01", SAME)

    # Re-open the just-modified cell and click Manual while an inline editor is
    # still active. The handler must stop the editor before collecting data.
    active_cell = review_cell(frame, "poc-row-01")
    active_cell.dblclick()
    inline_before_manual = frame.locator(".ag-cell-inline-editing").count()
    if inline_before_manual != 1:
        raise AssertionError("Expected exactly one active inline editor")
    click_manual(frame)
    first = wait_for_submit(page, 1)
    assert first["event_name"] == "manualUpdate"
    assert len(first["rows"]) == 30
    assert row_value(first, "poc-row-01") == SAME
    evidence["page_1"] = {
        "paging_text": page_1_text,
        "row_id_hidden": row_id_header_count == 0,
        "confirm_pinned_right": pinned_header.count() == 1,
        "multiline_preserved": "\n" in multiline_text,
        "inline_editor_before_manual": inline_before_manual,
        "event_name": first["event_name"],
        "returned_rows": len(first["rows"]),
        "row_id": "poc-row-01",
        "value": row_value(first, "poc-row-01"),
    }

    # Native pagination and second-page editing.
    next_page = frame.get_by_role("button", name="Next Page")
    next_page.click()
    frame.locator(
        '.ag-pinned-right-cols-container .ag-row[row-id="poc-row-11"]'
    ).wait_for(state="visible", timeout=5_000)
    choose_review(frame, "poc-row-11", DIFFERENT)
    click_manual(frame)
    second = wait_for_submit(page, 2)
    assert row_value(second, "poc-row-11") == DIFFERENT
    assert len(second["rows"]) == 30
    evidence["page_2"] = {
        "paging_text": frame.locator(".ag-paging-panel").inner_text(),
        "row_id": "poc-row-11",
        "value": row_value(second, "poc-row-11"),
        "returned_rows": len(second["rows"]),
    }

    # Sort descending, then edit a row whose identity moved.
    first_page = frame.get_by_role("button", name="First Page")
    first_page.click()
    signal_header = frame.locator(f'.ag-header-cell[col-id="{SIGNAL}"]')
    signal_header.locator(".ag-header-cell-label").click()
    signal_header.locator(".ag-header-cell-label").click()
    if signal_header.get_attribute("aria-sort") != "descending":
        raise AssertionError("Signal-name sort did not become descending")
    frame.locator(
        '.ag-pinned-right-cols-container .ag-row[row-id="poc-row-30"]'
    ).wait_for(state="visible", timeout=5_000)
    choose_review(frame, "poc-row-30", SAME)
    click_manual(frame)
    sorted_result = wait_for_submit(page, 3)
    assert row_value(sorted_result, "poc-row-30") == SAME
    evidence["sorting"] = {
        "aria_sort": signal_header.get_attribute("aria-sort"),
        "row_id": "poc-row-30",
        "value": row_value(sorted_result, "poc-row-30"),
        "returned_rows": len(sorted_result["rows"]),
    }

    # Use the actual header filter, not only the toolbar quick search.
    signal_header.hover()
    signal_header.locator(".ag-header-cell-filter-button").click()
    filter_input = frame.locator('input[aria-label="Filter Value"]:visible').last
    filter_input.fill("测试信号_05")
    filter_input.press("Escape")
    frame.locator(
        '.ag-pinned-right-cols-container .ag-row[row-id="poc-row-05"]'
    ).wait_for(state="visible", timeout=5_000)
    filtered_row_count = frame.locator(
        ".ag-pinned-right-cols-container .ag-row"
    ).count()
    choose_review(frame, "poc-row-05", DIFFERENT)
    click_manual(frame)
    filtered_result = wait_for_submit(page, 4)
    assert row_value(filtered_result, "poc-row-05") == DIFFERENT
    assert len(filtered_result["rows"]) == 30
    evidence["filtering"] = {
        "filter_value": "测试信号_05",
        "visible_rows": filtered_row_count,
        "row_id": "poc-row-05",
        "value": row_value(filtered_result, "poc-row-05"),
        "returned_rows": len(filtered_result["rows"]),
    }

    # Clear filter and change page size through the native selector.
    signal_header.hover()
    signal_header.locator(".ag-header-cell-filter-button").click()
    filter_inputs = frame.locator('input[aria-label="Filter Value"]:visible')
    populated_filter = None
    for index in range(filter_inputs.count()):
        candidate = filter_inputs.nth(index)
        if candidate.input_value():
            populated_filter = candidate
            break
    if populated_filter is None:
        raise AssertionError("Could not locate the populated signal-name filter")
    populated_filter.fill("")
    page.keyboard.press("Escape")
    deadline = time.time() + 5
    while time.time() < deadline:
        if "1 to 10 of 30" in frame.locator(".ag-paging-panel").inner_text():
            break
        time.sleep(0.1)
    else:
        raise AssertionError("Clearing the signal-name filter did not restore all rows")
    signal_header.locator(".ag-header-cell-label").click()
    if signal_header.get_attribute("aria-sort") not in (None, "none"):
        raise AssertionError("Signal-name sort did not return to the unsorted state")
    page_size = frame.get_by_role("combobox", name="Page Size")
    page_size.click()
    frame.get_by_role("option", name="5", exact=True).click()
    paging_panel = frame.locator(".ag-paging-panel")
    deadline = time.time() + 5
    while time.time() < deadline:
        if "Page 1 of 6" in paging_panel.inner_text():
            break
        time.sleep(0.1)
    else:
        raise AssertionError(
            f"Page Size 5 was not applied; paging panel={paging_panel.inner_text()!r}"
        )
    evidence["page_size"] = {
        "selected": page_size.inner_text(),
        "paging_text": paging_panel.inner_text(),
        "visible_rows": frame.locator(
            ".ag-pinned-right-cols-container .ag-row"
        ).count(),
    }

    # Multiple rows in one Manual submission.
    choose_review(frame, "poc-row-02", SAME)
    choose_review(frame, "poc-row-03", DIFFERENT)
    click_manual(frame)
    multirow = wait_for_submit(page, 5)
    assert row_value(multirow, "poc-row-02") == SAME
    assert row_value(multirow, "poc-row-03") == DIFFERENT
    evidence["multirow"] = {
        "submit_count": multirow["submit_count"],
        "values": {
            "poc-row-02": row_value(multirow, "poc-row-02"),
            "poc-row-03": row_value(multirow, "poc-row-03"),
        },
        "returned_rows": len(multirow["rows"]),
    }

    # Change one row back to the original value.
    choose_review(frame, "poc-row-02", PENDING)
    click_manual(frame)
    changed_back = wait_for_submit(page, 6)
    assert row_value(changed_back, "poc-row-02") == PENDING
    evidence["change_back"] = {
        "row_id": "poc-row-02",
        "value": row_value(changed_back, "poc-row-02"),
        "returned_rows": len(changed_back["rows"]),
    }

    # A second Manual with no further edits must return one complete, current
    # dataset without duplicated or stale rows.
    click_manual(frame)
    consecutive = wait_for_submit(page, 7)
    row_ids = [row["row_id"] for row in consecutive["rows"]]
    assert len(row_ids) == 30
    assert len(set(row_ids)) == 30
    assert row_value(consecutive, "poc-row-02") == PENDING
    assert row_value(consecutive, "poc-row-03") == DIFFERENT
    evidence["consecutive_submit"] = {
        "submit_count": consecutive["submit_count"],
        "returned_rows": len(row_ids),
        "unique_row_ids": len(set(row_ids)),
        "row_02": row_value(consecutive, "poc-row-02"),
        "row_03": row_value(consecutive, "poc-row-03"),
    }
    evidence["manual_response_excerpt"] = {
        "event_name": first["event_name"],
        "submit_count": first["submit_count"],
        "row": next(
            row for row in first["rows"] if row["row_id"] == "poc-row-01"
        ),
    }
    evidence["console_errors"] = console_errors
    context.close()
    browser.close()
    return evidence


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8510")
    parser.add_argument(
        "--browser", choices=("chrome", "edge", "all"), default="all"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    requested = (
        [args.browser] if args.browser != "all" else ["chrome", "edge"]
    )
    paths = {"chrome": CHROME, "edge": EDGE}
    results: dict[str, Any] = {}
    with sync_playwright() as playwright:
        for name in requested:
            executable = paths[name]
            if not executable.is_file():
                results[name] = {
                    "skipped": True,
                    "reason": f"Browser executable not found: {executable}",
                }
                continue
            results[name] = run_browser(
                playwright.chromium, executable, args.url
            )
            results[name]["skipped"] = False

    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
