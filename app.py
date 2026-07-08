from __future__ import annotations

import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

from core import run_all
from core.ai_review import run_ai_review
from core.pipeline import OUTPUT_FILENAMES

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
    st.caption("本地 Streamlit Demo：封装 legacy 脚本流程；AI 复核仅作为人工审核参考，不修改原始差异。")

    enable_ai_review = st.checkbox("启用 AI 辅助复核", value=False)
    st.caption("AI 仅对“信号值描述/单位”等文本差异进行辅助判断，不会修改原始差异结果；所有差异仍需人工审核。")

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

            status.write("正在执行 01/02/03 legacy 流程，生成全量、去重和最终差异结果...")
            pipeline_result = run_all(input_40_dir, input_51_dir, output_dir)
            progress.progress(82)

            status.write("正在生成 AI辅助复核与人工审核明细 sheet...")
            compare_file = pipeline_result["files"]["compare"]
            ai_stats = run_ai_review(compare_file, enable_ai=enable_ai_review)
            progress.progress(100)
            status.write("处理完成。")

            st.subheader("结果统计")
            stats_df = pd.DataFrame([{"指标": key, "数量": value} for key, value in pipeline_result["statistics"].items()])
            st.dataframe(stats_df, hide_index=True, use_container_width=True)

            st.subheader("AI/人工审核明细统计")
            ai_stat_labels = {
                "total_review_items": "人工审核明细总数",
                "ai_reviewed_count": "AI实际复核数",
                "ai_skipped_count": "未进行AI复核数",
                "suspicious_same_count": "疑似一致数",
                "typo_count": "疑似错别字数",
                "semantic_similar_count": "疑似语义相近数",
                "real_diff_count": "真实差异数",
                "unknown_count": "无法判断数",
                "not_applicable_count": "不适用数",
                "llm_disabled_count": "AI未启用数",
            }
            ai_stats_df = pd.DataFrame([
                {"指标": label, "数量": ai_stats.get(key, 0)}
                for key, label in ai_stat_labels.items()
            ])
            st.dataframe(ai_stats_df, hide_index=True, use_container_width=True)
            if enable_ai_review and ai_stats.get("llm_disabled_count", 0) > 0 and not ai_stats.get("warnings"):
                st.warning("已勾选 AI 辅助复核，但未检测到 LLM_ENABLED=true。请确认 .env 位于项目根目录、文件名不是 .env.txt、已重新启动 start_demo.bat，并已安装 python-dotenv 或使用当前版本的内置 .env 读取。")
            for warning in ai_stats.get("warnings", []):
                st.warning(warning)

            with st.expander("查看执行日志"):
                for result in pipeline_result["logs"]:
                    st.markdown(f"**{result['script']}**（returncode={result['returncode']}）")
                    st.code(result.get("stdout") or "<empty stdout>", language="text")
                    if result.get("stderr"):
                        st.code(result["stderr"], language="text")

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
