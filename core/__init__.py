"""Pipeline entrypoints for the local matrix signal diff demo."""

from .pipeline import run_all, run_compare, run_dedup, run_extract

__all__ = ["run_all", "run_extract", "run_dedup", "run_compare"]
