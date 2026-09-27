"""Net load and outage capacity, read only from an as_of forecast view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import datetime as dt

import pandas as pd

from harness.observation import ForecastQuery

# Non-Spin is named more than one way across ERCOT reports.
NSPIN_NAMES = ("NSPIN", "NONSPIN", "Non-Spin", "NSpin")


def feature_history(moment: pd.Timestamp, prior: pd.DataFrame, forecasts: ForecastQuery | None,
                    horizon: dt.timedelta) -> list[dict[str, Any]]:
    """Net load and outage for each past hour, from `as_of` at that hour."""
    if forecasts is None or prior.empty:
        return []
    hours = sorted({pd.Timestamp(ts).floor("h") for ts in prior.index})
    rows = []
    for hour in hours:
        feature = net_load_at(forecasts.as_of(pd.Timestamp(hour), horizon=horizon), hour)
        if feature is None:
            continue
        net_load, outage = feature
        rows.append({
            "valid_time": pd.Timestamp(hour).tz_convert("UTC").isoformat(),
            "net_load_mw": net_load,
            "outage_mw": outage,
        })
    return rows


def net_load_at(forecasts: Mapping[str, list[dict[str, Any]]], hour: pd.Timestamp,
                ) -> tuple[float, float] | None:
    """(load − wind − solar, outage capacity) at `hour`, or None when an input is missing.

    Load, wind and solar are the in-use system series. Outage capacity is the
    sum of zonal total resource outages.
    """
    hour = pd.Timestamp(hour).tz_convert("UTC")
    load = _select(forecasts.get("load_by_model_zone", []), hour, lambda series: series.endswith("|system"))
    wind = _select(forecasts.get("wind_system", []), hour, lambda series: series == "stwpf|system")
    solar = _select(forecasts.get("solar_system", []), hour, lambda series: series == "stppf|system")
    outage = _outage(forecasts.get("outage_capacity", []), hour)
    if load is None or wind is None or solar is None or outage is None:
        return None
    return load - wind - solar, outage


def _select(rows: list[dict[str, Any]], hour: pd.Timestamp, match) -> float | None:
    hits = [row for row in rows if _at(row, hour) and match(str(row["series"]))]
    if not hits:
        return None
    in_use = [row for row in hits if row.get("in_use") is True]
    pool = in_use or hits
    chosen = min(pool, key=lambda row: str(row["series"]))
    return float(chosen["value"])


def _outage(rows: list[dict[str, Any]], hour: pd.Timestamp) -> float | None:
    hits = [row for row in rows if _at(row, hour) and str(row["series"]).startswith("total_resource|")]
    if not hits:
        return None
    return float(sum(float(row["value"]) for row in hits))


def _at(row: Mapping[str, Any], hour: pd.Timestamp) -> bool:
    valid = pd.Timestamp(row["valid_time"])
    if valid.tzinfo is None:
        return False
    return bool(valid.tz_convert("UTC") == hour)
