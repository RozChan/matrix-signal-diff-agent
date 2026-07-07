# matrix-signal-diff-agent

`matrix-signal-diff-agent` is a local demo tool for EEA 4.0 / 5.1 vehicle signal matrix comparison, same-name signal deduplication, and signal definition difference detection.

The first-stage engineering goal is to keep the existing Excel business rules unchanged while exposing the original processing scripts through reusable Python functions and a local Streamlit page.

## Project Purpose

This project compares EEA 4.0 and EEA 5.1 vehicle signal matrix Excel files.

During architecture upgrades, a signal name can remain unchanged while fields such as signal length, resolution, offset, physical min/max value, unit, or signal value description change. If these differences are missed by vehicle-side systems, they may cause integration or runtime issues.

The tool detects signals with the same name, or equivalent meaning, but different signal definitions between EEA 4.0 and EEA 5.1.

## Current Architecture

The Streamlit demo calls the reusable `core` package:

```text
app.py
core/
  __init__.py
  pipeline.py
```

The `core` package provides these entry points:

```python
run_extract(input_40_dir, input_51_dir, output_dir)
run_dedup(output_dir)
run_compare(output_dir)
run_all(input_40_dir, input_51_dir, output_dir)
```

The wrapper layer does **not** rewrite the Excel business logic. It delegates to the original repository scripts when they are present:

- `01_extract_full_matrix_local_v13.py`
- `02_generate_dedup_signals_local_v13.py`
- `03_generate_compare_file5_local_v13.py`
- `common_matrix_utils_local_v13.py`

If the legacy scripts use relative paths such as `input/4.0`, `input/5.1`, and `output`, the wrapper runs them from the task directory so the original command-line assumptions continue to work.

## Business Rules Preserved

The engineering wrapper and Streamlit page are designed to preserve the existing business rules:

1. Do not collect `History` or other non-matrix sheets.
2. `R/r` must be normalized to `r`.
3. `T/t/S/s` must be normalized to `s`.
4. EEA 4.0 ECU status must use grouped format:

```text
EMS_PHEV:s(HighRegulationArea:s,LowRegulationArea:s)
```

5. EEA 4.0 send/receive ECU summary must also use grouped format:

```text
EMS_PHEV(HighRegulationArea,LowRegulationArea)
```

6. EEA 5.1 ECU status remains line-by-line.
7. Deduplication is based on signal name.
8. Signal definition fields keep the first occurrence after deduplication.
9. Source files and ECU status are aggregated during deduplication.
10. Signal value description comparison keeps support for enum order differences, `0x01` vs `0x1`, and range enums such as `0x1~0x6: Reserved`.

## Install Dependencies

Create and activate a Python virtual environment if needed, then install dependencies:

```bash
pip install -r requirements.txt
```

Dependencies:

- Python
- Streamlit
- openpyxl
- pandas

No database, Feishu integration, LLM call, or complex frontend framework is required for this demo.

## Start the Streamlit Demo

On Windows, double-click:

```text
start_demo.bat
```

Or run manually from the repository root:

```bash
python -m streamlit run app.py
```

The page title is:

```text
EEA 4.0/5.1 矩阵同一信号差异识别工具
```

## Upload Files

In the Streamlit page:

1. Upload one or more EEA 4.0 matrix Excel files in the 4.0 upload area.
2. Upload one or more EEA 5.1 matrix Excel files in the 5.1 upload area.
3. Supported file extensions are `.xlsx` and `.xlsm`.
4. Click `开始识别`.

For each run, the app creates a task directory:

```text
temp/<task_id>/input/4.0
temp/<task_id>/input/5.1
temp/<task_id>/output
```

Uploaded files are saved into the corresponding input directories, and `core.run_all(input_40_dir, input_51_dir, output_dir)` runs the full pipeline.

## Output Files

The output filenames remain unchanged:

```text
26R1 4.0全量信号矩阵清单.xlsx
26R2 5.1全量信号矩阵清单.xlsx
26R1 4.0全量信号-同名去重后.xlsx
26R2 5.1全量信号-同名去重后.xlsx
4.0和5.1同一信号差异点识别.xlsx
```

Files are generated under:

```text
temp/<task_id>/output/
```

The Streamlit page provides:

- one download button for each result file;
- one download-all ZIP button.

## Statistics Display

After processing finishes, the page displays these basic statistics by reading generated Excel files:

- 4.0 full signal count;
- 5.1 full signal count;
- 4.0 deduplicated signal count;
- 5.1 deduplicated signal count;
- exact same-name match count;
- sheet1 difference row count;
- sheet2 VCU-HCU difference row count.

## Command-Line Usage

The original `01`, `02`, and `03` scripts can remain available as command-line entry points.

The new wrapper functions can also be called from Python:

```python
from core import run_all

run_all(
    input_40_dir="input/4.0",
    input_51_dir="input/5.1",
    output_dir="output",
)
```

## Troubleshooting

If processing fails, the Streamlit page shows the exception and a clear error message. Common checks:

1. Confirm the original business scripts are present in the repository root.
2. Confirm dependencies are installed with `pip install -r requirements.txt`.
3. Confirm uploaded files are valid `.xlsx` or `.xlsm` files.
4. Confirm matrix sheets are not named as non-matrix sheets such as `History` or revision logs.
5. Confirm the process has permission to create `temp/<task_id>/` and write result files.
6. If no real sample Excel files are available, validate at least that Python files compile and `core` can be imported.

## Development Notes

This first-stage demo is an engineering wrapper around the original scripts. It does not intentionally change output field names, sheet names, output filenames, ECU formatting rules, deduplication rules, or signal value description comparison rules.
