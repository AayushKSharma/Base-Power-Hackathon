"""Per-MW-of-fleet quantile shares from Base-actual SCED rows.

Flexible MW is real power consumption minus low power consumption, summed across
the QSE's Aggregate Load Resources on each SCED run. Headroom is max power
consumption minus low power consumption, summed the same way. The share is
flexible MW per MW of that headroom, which stays on the fleet's size so a
scenario can scale it by changing how many homes it has.
"""

from __future__ import annotations

from typing import NamedTuple, TypedDict, cast

import numpy as np
import pandas as pd

LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
NAMES = ("P10", "P25", "P50", "P75", "P90")
# An empty (month, hour) uses that hour's samples from other months, or every
# sample when the hour never occurs.
FALLBACK_HOUR = "hour"
FALLBACK_ALL = "all"

ShareRow = dict[str, float]


class FallbackBucket(TypedDict):
    month: int
    hour: int
    fallback: str


class QuantileMockSet(NamedTuple):
    shares: pd.DataFrame
    fallback_buckets: list[FallbackBucket]
    hour_of_day: pd.DataFrame  # only hours that have at least one run
    clipped_runs: int
    skipped_runs: int


def quantile_shares(rows: pd.DataFrame, qse: str) -> QuantileMockSet:
    """P10..P90 of flexible MW per MW of headroom, by CPT month and hour.

    Quantiles are computed on the raw ratio, then clamped into [0, 1] so the
    share CSV can be loaded. Runs whose ratio was outside that range are
    counted in `clipped_runs`. Runs with no positive headroom are left out and
    counted in `skipped_runs`.
    """
    profile, skipped = _profile(rows, qse)
    if profile.empty:
        raise ValueError(f"no SCED runs for QSE {qse}")
    observed: dict[tuple[int, int], ShareRow] = {}
    for key, group in profile.groupby(["month", "hour"], sort=True):
        month, hour = cast(tuple[int, int], key)
        observed[(int(month), int(hour))] = _levels(group)
    by_hour = {int(cast(int, hour)): _levels(group) for hour, group in profile.groupby("hour", sort=True)}
    overall = _levels(profile)
    hour_of_day = pd.DataFrame(
        [{"hour": hour, **_clamp(by_hour[hour])} for hour in sorted(by_hour)],
        columns=["hour", *NAMES],
    )
    records: list[dict[str, float]] = []
    flagged: list[FallbackBucket] = []
    for month in range(1, 13):
        for hour in range(24):
            values = observed.get((month, hour))
            if values is None and hour in by_hour:
                values = by_hour[hour]
                flagged.append({"month": month, "hour": hour, "fallback": FALLBACK_HOUR})
            elif values is None:
                values = overall
                flagged.append({"month": month, "hour": hour, "fallback": FALLBACK_ALL})
            records.append({"month": month, "hour": hour, **_clamp(values)})
    shares = pd.DataFrame(records, columns=["month", "hour", *NAMES])
    return QuantileMockSet(shares, flagged, hour_of_day, int(profile["clipped"].sum()), skipped)


def _profile(rows: pd.DataFrame, qse: str) -> tuple[pd.DataFrame, int]:
    base = rows.loc[rows["qse"] == qse].copy()
    base["flexible_mw"] = base["real_power_consumption_mw"] - base["low_power_consumption_mw"]
    base["headroom_mw"] = base["max_power_consumption_mw"] - base["low_power_consumption_mw"]
    summed = base.groupby("sced_time_utc", sort=True).agg(
        flexible_mw=("flexible_mw", "sum"),
        headroom_mw=("headroom_mw", "sum"),
        sced_time_cpt=("sced_time_cpt", "first"),
    )
    usable = (summed["headroom_mw"] > 0) & summed["flexible_mw"].notna()
    skipped = int((~usable).sum())
    summed = summed.loc[usable].copy()
    raw = summed["flexible_mw"] / summed["headroom_mw"]
    summed["share"] = raw
    summed["clipped"] = (raw < 0) | (raw > 1)
    summed["month"] = summed["sced_time_cpt"].dt.month.astype(int)
    summed["hour"] = summed["sced_time_cpt"].dt.hour.astype(int)
    return summed, skipped


def _levels(group: pd.DataFrame) -> ShareRow:
    values = np.quantile(group["share"].to_numpy(dtype=float), LEVELS, method="linear")
    return {name: float(value) for name, value in zip(NAMES, values, strict=True)}


def _clamp(row: ShareRow) -> ShareRow:
    """The share file only stores [0, 1]. Clamp each quantile, not each run."""
    return {name: min(1.0, max(0.0, row[name])) for name in NAMES}
