# Repository maintenance instructions

When changing any judgment, extraction, deduplication, comparison, normalization, output field, output file name, or output sheet name logic in the legacy matrix scripts, also update `JUDGMENT_RULES_V1.txt` in the repository root in the same change.

The rule document must stay aligned with these scripts:

- `common_matrix_utils_local_v13.py`
- `01_extract_full_matrix_local_v13.py`
- `02_generate_dedup_signals_local_v13.py`
- `03_generate_compare_file5_local_v13.py`
