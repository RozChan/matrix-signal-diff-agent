"""Pipeline and review entrypoints for the local matrix signal diff demo."""

from .ai_review import run_ai_review
from .pipeline import run_all, run_compare, run_dedup, run_extract
from .final_export import export_final_review_result

__all__ = ["run_all", "run_extract", "run_dedup", "run_compare", "run_ai_review", "export_final_review_result"]
