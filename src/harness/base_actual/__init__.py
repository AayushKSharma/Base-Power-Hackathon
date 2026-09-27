"""Base-actual dataset: Base's Aggregate Load Resources in ERCOT's 60-Day SCED Disclosure.

    from harness.base_actual import load_base_actual
    df = load_base_actual("2026-07-01", "2026-07-28")

Build it first with `python -m harness.base_actual build`. See data/README.md.
"""

from harness.base_actual.analysis import (
    DEFAULT_DISPATCH_THRESHOLD_MW,
    daily_summary,
    dispatch_down,
    dispatch_down_events,
    grade_delivery,
)
from harness.base_actual.dataset import BaseActualBuild, build_base_actual, load_base_actual
from harness.base_actual.source import BASE_QSE

__all__ = [
    "BASE_QSE",
    "DEFAULT_DISPATCH_THRESHOLD_MW",
    "BaseActualBuild",
    "build_base_actual",
    "daily_summary",
    "dispatch_down",
    "dispatch_down_events",
    "grade_delivery",
    "load_base_actual",
]
