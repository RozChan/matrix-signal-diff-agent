"""Core pipeline entry points for the matrix signal difference demo."""

from .pipeline import PipelineResult, run_all, run_compare, run_dedup, run_extract

__all__ = [
    "PipelineResult",
    "run_extract",
    "run_dedup",
    "run_compare",
    "run_all",
]
