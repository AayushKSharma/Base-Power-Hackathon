"""ERCOT market dataset on a 5-minute interval grid (post-RTC+B).

    from harness.market import load_intervals
    df = load_intervals("2026-08-01", "2026-08-31")

Build it first with `python -m harness.market build`. Columns are documented in
data/README.md.
"""

from harness.market.dataset import (
    BuildResult,
    build_dataset,
    load_asdc,
    load_intervals,
    quality_report,
)
from harness.market.quality import QualityReport
from harness.market.scarcity import PriceThresholdScarcity, ScarcityProxy

__all__ = [
    "BuildResult",
    "PriceThresholdScarcity",
    "QualityReport",
    "ScarcityProxy",
    "build_dataset",
    "load_asdc",
    "load_intervals",
    "quality_report",
]
