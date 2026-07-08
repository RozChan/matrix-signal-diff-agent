from __future__ import annotations

import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

from core import run_compare, run_dedup, run_extract
from core.pipeline import OUTPUT_FILENAMES, collect_statistics

APP_ROOT = Path(__file__).resolve().parent
TEMP_ROOT = APP_ROOT / "temp"
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _save_uploads(files: Iterable, target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for uploaded_file in files:
        filename = _safe_filename(uploaded_file.name)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型：{uploaded_file.name}，仅支持 .xlsx / .xlsm")
        target = target_dir / filename
        target.write_bytes(uploaded_file.getbuffer())
        saved.append(target)
    return saved


def _zip_outputs(output_dir: Path, zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename in OUTPUT_FILENAMES.values():
            path = output_dir / filename
            if path.exists():
                zf.write(path, arcname=filename)
    return zip_path


def _show_downloads(output_dir: Path, task_dir: Path) -> None:
    st.subheader("结果文件下载")
    for filename in OUTPUT_FILENAMES.values():
        path = output_dir / filename
        if not path.exists():
            st.warning(f"未找到输出文件：{filename}")
            continue
        with path.open("rb") as fh:
            st.download_button(
                label=f"下载 {filename}",
                data=fh.read(),
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download-{task_dir.name}-{filename}",
            )

    zip_path = _zip_outputs(output_dir, task_dir / "全部结果文件.zip")
    with zip_path.open("rb") as fh:
        st.download_button(
            label="下载全部结果 zip",
            data=fh.read(),
            file_name="matrix_signal_diff_results.zip",
            mime="application/zip",
            key=f"download-zip-{task_dir.name}",
        )


def main() -> None:
    st.set_page_config(page_title="EEA 4.0/5.1 矩阵同一信号差异识别工具", layout="wide")
    st.title("EEA 4.0/5.1 矩阵同一信号差异识别工具")
    st.caption("本地 Streamlit Demo：仅封装 legacy 脚本流程，不接飞书、不接真实大模型 API。")

    files_40 = st.file_uploader("上传 4.0 矩阵文件（支持多个 .xlsx / .xlsm）", type=["xlsx", "xlsm"], accept_multiple_files=True)
    files_51 = st.file_uploader("上传 5.1 矩阵文件（支持多个 .xlsx / .xlsm）", type=["xlsx", "xlsm"], accept_multiple_files=True)

    disabled = not files_40 or not files_51
    if disabled:
        st.info("请分别上传至少 1 个 4.0 和 5.1 矩阵 Excel 文件后开始识别。")

    if st.button("开始识别", type="primary", disabled=disabled):
        task_id = uuid.uuid4().hex
        task_dir = TEMP_ROOT / task_id
        input_40_dir = task_dir / "input" / "4.0"
        input_51_dir = task_dir / "input" / "5.1"
        output_dir = task_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        progress = st.progress(0)
        status = st.empty()
        log_box = st.container()

        try:
            status.write("已创建临时任务目录，正在保存上传文件...")
            saved_40 = _save_uploads(files_40, input_40_dir)
            saved_51 = _save_uploads(files_51, input_51_dir)
            progress.progress(15)
            st.success(f"已保存上传文件：4.0={len(saved_40)} 个，5.1={len(saved_51)} 个。任务目录：{task_dir}")

            status.write("正在生成 4.0 / 5.1 全量信号矩阵清单...")
            extract_result = run_extract(input_40_dir, input_51_dir, output_dir)
            progress.progress(45)

            status.write("正在生成同名去重结果...")
            dedup_result = run_dedup(output_dir)
            progress.progress(70)

            status.write("正在生成最终差异识别结果...")
            compare_result = run_compare(output_dir)
            progress.progress(90)

            stats = collect_statistics(output_dir)
            progress.progress(100)
            status.write("处理完成。")

            st.subheader("结果统计")
            stats_df = pd.DataFrame([{"指标": key, "数量": value} for key, value in stats.items()])
            st.dataframe(stats_df, hide_index=True, use_container_width=True)

            with st.expander("查看执行日志"):
                for result in [extract_result, dedup_result, compare_result]:
                    st.markdown(f"**{result.script}**（returncode={result.returncode}）")
                    st.code(result.stdout or "<empty stdout>", language="text")
                    if result.stderr:
                        st.code(result.stderr, language="text")

            _show_downloads(output_dir, task_dir)

        except Exception as exc:  # noqa: BLE001 - Streamlit needs to render any pipeline failure.
            progress.progress(100)
            status.write("处理失败。")
            st.error(str(exc))
            detail = traceback.format_exc()
            with log_box.expander("错误详情（可复制）", expanded=True):
                st.code(detail, language="text")


if __name__ == "__main__":
    main()
