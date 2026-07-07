# matrix-signal-diff-agent

A local demo tool for EEA 4.0 / 5.1 signal matrix comparison, deduplication, and signal definition difference detection.

## Project Background

This project is used to compare EEA 4.0 and EEA 5.1 vehicle signal matrix Excel files.

In some architecture upgrade cases, the signal name remains unchanged, but signal definition fields such as signal length, resolution, offset, physical min/max value, unit, or signal value description may change. If these changes are not identified by the vehicle-side system, they may cause vehicle issues.

The goal of this tool is to automatically detect signals that have the same name, or equivalent meaning, but different signal definitions between EEA 4.0 and EEA 5.1.

## Current Status

The current repository contains four Python scripts. These scripts already implement the core business logic.

Current scripts:

- `common_matrix_utils_local_v13.py`
- `01_extract_full_matrix_local_v13.py`
- `02_generate_dedup_signals_local_v13.py`
- `03_generate_compare_file5_local_v13.py`

The next development step is to refactor these scripts into reusable core modules and build a local Streamlit demo.

## Existing Script Responsibilities

### 1. `common_matrix_utils_local_v13.py`

Common utility module.

Main responsibilities:

- Clean Excel cell text.
- Normalize table headers.
- Normalize ECU send/receive status.
- Compare numeric fields.
- Normalize and compare signal value descriptions.
- Support enum description comparison, including:
  - enum order differences;
  - `0x01` vs `0x1`;
  - range enum format such as `0x1~0x6: Reserved`.

Current ECU status normalization rule:

- `R/r` -> `r`, meaning receive.
- `T/t/S/s` -> `s`, meaning send.

It also supports parsing EEA 4.0 grouped ECU format, for example:

```text
EMS_PHEV:s(HighRegulationArea:s,LowRegulationArea:s)
```

### 2. `01_extract_full_matrix_local_v13.py`

Extracts full signal matrix lists from local Excel files.

Input directories:

```text
input/4.0
input/5.1
```

Main responsibilities:

- Read all `.xlsx` and `.xlsm` files under `input/4.0` and `input/5.1`.
- Automatically identify valid matrix sheets.
- Exclude non-matrix sheets such as `History`, revision logs, and description sheets.
- Extract signal information from valid matrix sheets.
- Preserve source file, source sheet, and source row.
- Extract signal definition fields:
  - signal name;
  - signal length;
  - resolution;
  - offset;
  - physical min value;
  - physical max value;
  - unit;
  - signal value description.
- Extract ECU send/receive status columns from the right side of the matrix.

Important business rules:

- For EEA 4.0, ECU status should be output using grouped format:

```text
EMS_PHEV:s(HighRegulationArea:s,LowRegulationArea:s)
```

- For EEA 4.0, send ECU summary / receive ECU summary should also use grouped format:

```text
EMS_PHEV(HighRegulationArea,LowRegulationArea)
```

- For EEA 5.1, ECU status remains line-by-line:

```text
VCU_5IC_DK:s
VCU_5IH_DK:s
VCU_L4_DK:s
VCU_TA_DK:s
```

Output files:

```text
output/26R1 4.0全量信号矩阵清单.xlsx
output/26R2 5.1全量信号矩阵清单.xlsx
```

### 3. `02_generate_dedup_signals_local_v13.py`

Generates deduplicated signal lists.

Input files:

```text
output/26R1 4.0全量信号矩阵清单.xlsx
output/26R2 5.1全量信号矩阵清单.xlsx
```

Main responsibilities:

- Deduplicate signals by signal name.
- Keep the first occurrence as the main signal definition.
- Aggregate all source files where the same signal appears.
- Aggregate all ECU send/receive status information.
- Preserve EEA 4.0 grouped ECU format.

Output files:

```text
output/26R1 4.0全量信号-同名去重后.xlsx
output/26R2 5.1全量信号-同名去重后.xlsx
```

### 4. `03_generate_compare_file5_local_v13.py`

Generates final signal difference comparison result.

Input files:

```text
output/26R1 4.0全量信号-同名去重后.xlsx
output/26R2 5.1全量信号-同名去重后.xlsx
```

Main responsibilities:

- Compare EEA 4.0 and EEA 5.1 deduplicated signals.
- Sheet 1: exact same-name signal comparison.
- Sheet 2: VCU/HCU prefix-stripped matching.
- Identify differences in the following fields:
  - signal length;
  - resolution;
  - offset;
  - physical min value;
  - physical max value;
  - unit;
  - signal value description.

ECU status fields are preserved for traceability but are not currently used as difference criteria.

Output file:

```text
output/4.0和5.1同一信号差异点识别.xlsx
```

## Current Manual Usage

Place EEA 4.0 Excel files in:

```text
input/4.0
```

Place EEA 5.1 Excel files in:

```text
input/5.1
```

Run scripts in order:

```bash
python 01_extract_full_matrix_local_v13.py
python 02_generate_dedup_signals_local_v13.py
python 03_generate_compare_file5_local_v13.py
```

Generated files are saved under:

```text
output/
```

## Target Demo Version

The next target is to build a local Streamlit demo.

Expected user flow:

1. Open a local web page.
2. Upload multiple EEA 4.0 matrix Excel files.
3. Upload multiple EEA 5.1 matrix Excel files.
4. Click "Start Comparison".
5. The system automatically runs the complete pipeline.
6. The page shows progress and statistics.
7. The user can download all intermediate and final result files.

## Target Technical Stack

Recommended stack:

- Python
- Streamlit
- openpyxl
- pandas, optional for statistics display
- no database
- no Feishu integration in the first version
- no real LLM call in the first version

## Refactoring Requirements

The existing business logic should not be rewritten from scratch.

The refactoring should:

1. Create a `core/` directory.
2. Move reusable logic into importable modules.
3. Keep the original command-line scripts available.
4. Avoid hardcoded paths such as `D:\signal_compare`.
5. Support function parameters:
   - `input_40_dir`
   - `input_51_dir`
   - `output_dir`

Suggested core functions:

```python
run_extract(input_40_dir, input_51_dir, output_dir)
run_dedup(output_dir)
run_compare(output_dir)
run_all(input_40_dir, input_51_dir, output_dir)
```

## Streamlit Demo Requirements

The Streamlit app should include:

- Title: `EEA 4.0/5.1 矩阵同一信号差异识别工具`
- Upload area for EEA 4.0 Excel files.
- Upload area for EEA 5.1 Excel files.
- Start button.
- Progress display.
- Result statistics.
- Download buttons for each result file.
- Download-all ZIP button.
- Clear error messages if processing fails.

## Expected Output Files

The output file names must remain unchanged:

```text
26R1 4.0全量信号矩阵清单.xlsx
26R2 5.1全量信号矩阵清单.xlsx
26R1 4.0全量信号-同名去重后.xlsx
26R2 5.1全量信号-同名去重后.xlsx
4.0和5.1同一信号差异点识别.xlsx
```

## Business Rules That Must Not Be Changed

The following business rules must be preserved:

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

6. EEA 5.1 ECU status should remain line-by-line.
7. Deduplication is based on signal name.
8. Signal definition fields keep the first occurrence after deduplication.
9. Source files and ECU status must be aggregated during deduplication.
10. Signal value description comparison must support:
    - enum order differences;
    - `0x01` vs `0x1`;
    - range enum format such as `0x1~0x6: Reserved`.

## Future Roadmap

### Phase 1

Local Streamlit demo.

### Phase 2

Add LLM-assisted review for suspicious text differences, such as typos or semantically similar descriptions.

### Phase 3

Package local demo with a double-click startup script.

### Phase 4

Refactor core logic into a FastAPI service.

### Phase 5

Integrate with Feishu bot for task triggering and result delivery.
