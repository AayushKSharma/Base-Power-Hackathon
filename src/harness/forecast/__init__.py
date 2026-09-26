"""Point-in-time forecast inputs. Policies read them only through `as_of`.

    from harness.forecast import as_of, build_forecasts

    build_forecasts("2026-03-01", "2026-03-08")
    as_of("2026-03-08T08:00:00-05:00")
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from harness.forecast.catalog import DEFAULT_HORIZON, INPUTS
from harness.forecast.dataset import (
    BuildResult,
    build_forecasts,
    default_raw_dir,
    default_store_dir,
    load_forecasts,
)
from harness.forecast.store import ForecastRow, ForecastStore

__all__ = [
    "DEFAULT_HORIZON",
    "INPUTS",
    "BuildResult",
    "ForecastRow",
    "ForecastStore",
    "as_of",
    "build_forecasts",
    "default_raw_dir",
    "default_store_dir",
    "load_forecasts",
]


def as_of(
    when: pd.Timestamp | str,
    inputs: list[str] | None = None,
    *,
    horizon: dt.timedelta = DEFAULT_HORIZON,
    store_dir: Path | str | None = None,
) -> dict[str, list[ForecastRow]]:
    """Latest vintage of each input posted at or before `when`, within `horizon`.

    `when` is timezone-aware (a decision time). Valid times are [when, when + horizon).
    """
    return ForecastStore(store_dir or default_store_dir()).as_of(pd.Timestamp(when), inputs, horizon=horizon)
