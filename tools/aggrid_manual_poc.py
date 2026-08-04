"""Isolated browser POC for the project-local AG Grid Manual submission."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st
from st_aggrid import (
    AgGrid,
    DataReturnMode,
    GridOptionsBuilder,
    GridUpdateMode,
    JsCode,
)


PENDING = "🔴 待选择（点击此处审核）"
SAME = "🟢 相同"
DIFFERENT = "🟢 不同"
OPTIONS = [PENDING, SAME, DIFFERENT]


def mock_rows(count: int = 30) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(1, count + 1):
        multiline_40 = (
            f"0x00: 状态{index}关闭\n"
            f"0x01: 状态{index}开启\n"
            "0x02-0x0F: 预留"
        )
        multiline_51 = (
            f"0x00: 状态{index}关闭\n"
            f"0x01: 状态{index}激活\n"
            "0x02-0x0F: 预留"
        )
        rows.append(
            {
                "row_id": f"poc-row-{index:02d}",
                "信号名": f"测试信号_{index:02d}",
                "EEA4.0字段值": multiline_40,
                "EEA5.1字段值": multiline_51,
                "人工确认": PENDING,
            }
        )
    return rows


def grid_options(frame: pd.DataFrame) -> dict[str, Any]:
    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_default_column(
        sortable=True,
        filter=True,
        resizable=True,
        suppressHeaderMenuButton=False,
    )
    builder.configure_column("row_id", hide=True)
    builder.configure_column("信号名", width=155, minWidth=130)
    for field in ("EEA4.0字段值", "EEA5.1字段值"):
        builder.configure_column(
            field,
            flex=1,
            minWidth=260,
            wrapText=True,
            autoHeight=True,
            tooltipField=field,
            cellStyle={"whiteSpace": "pre-line", "lineHeight": "1.35"},
        )
    builder.configure_column(
        "人工确认",
        editable=True,
        pinned="right",
        width=235,
        minWidth=235,
        maxWidth=235,
        cellEditor="agSelectCellEditor",
        cellEditorParams={"values": OPTIONS},
        cellStyle=JsCode(
            """
            function(params) {
                if (params.value && params.value.startsWith("🟢")) {
                    return {
                        color: "#137333",
                        backgroundColor: "#e6f4ea",
                        fontWeight: "700",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "flex-start",
                        lineHeight: "normal"
                    };
                }
                return {
                    color: "#b3261e",
                    backgroundColor: "#fce8e6",
                    fontWeight: "700",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "flex-start",
                    lineHeight: "normal"
                };
            }
            """
        ),
    )
    builder.configure_grid_options(
        pagination=True,
        paginationPageSize=10,
        paginationPageSizeSelector=[5, 10, 20, 50],
        animateRows=False,
        tooltipShowDelay=250,
        rowHeight=34,
        getRowId=JsCode("function(params) { return params.data.row_id; }"),
    )
    return builder.build()


def json_ready(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def main() -> None:
    st.set_page_config(page_title="AG Grid Manual POC", layout="wide")
    st.title("AG Grid Manual Update 隔离 POC")
    st.caption(
        "本页面只使用模拟数据，不读取审核任务、不写 review_state.json、"
        "不写历史库，也不调用正式保存后端。"
    )

    frame = pd.DataFrame(mock_rows())
    response = AgGrid(
        frame,
        gridOptions=grid_options(frame),
        height=620,
        update_mode=GridUpdateMode.MANUAL,
        update_on=[],
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        show_toolbar=True,
        show_search=True,
        show_download_button=False,
        theme="streamlit",
        key="aggrid-manual-poc-grid",
        try_to_convert_back_to_original_types=False,
        debug=False,
    )

    event_data = response.event_data or {}
    event_name = str(
        event_data.get("streamlitRerunEventTriggerName")
        or event_data.get("type")
        or ""
    )
    returned_frame = response.data
    returned_rows = (
        returned_frame.to_dict("records")
        if isinstance(returned_frame, pd.DataFrame)
        else []
    )

    if event_name == "manualUpdate":
        st.session_state["poc_manual_submit_count"] = (
            int(st.session_state.get("poc_manual_submit_count", 0)) + 1
        )
        st.session_state["poc_last_event"] = event_name
        st.session_state["poc_last_rows"] = returned_rows
        st.session_state["poc_last_event_data"] = json_ready(event_data)

    submit_count = int(st.session_state.get("poc_manual_submit_count", 0))
    last_event = str(st.session_state.get("poc_last_event", ""))
    last_rows = list(st.session_state.get("poc_last_rows", []))
    selected_rows = [
        {"row_id": row.get("row_id"), "人工确认": row.get("人工确认")}
        for row in last_rows
        if row.get("人工确认") != PENDING
    ]

    st.subheader("Python 实际收到的 Manual 返回")
    left, middle, right = st.columns(3)
    left.metric("Manual提交次数", submit_count)
    middle.metric("event名称", last_event or "尚未提交")
    right.metric("返回行数", len(last_rows))
    st.write("已修改 row_id 与人工确认值")
    st.json(selected_rows, expanded=True)
    with st.expander("完整 response JSON", expanded=True):
        result_payload = {
            "event_name": last_event,
            "submit_count": submit_count,
            "event_data": st.session_state.get("poc_last_event_data", {}),
            "rows": last_rows,
        }
        st.json(result_payload, expanded=True)
    st.code(
        "POC_RESULT_JSON="
        + json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")),
        language=None,
    )


if __name__ == "__main__":
    main()
