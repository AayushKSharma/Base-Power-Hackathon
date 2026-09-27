"""Dispatch-down events and daily per-resource stats over Base-actual rows."""

from __future__ import annotations

from typing import cast

import pandas as pd

from harness.market.catalog import CPT
from harness.scenario import SPD_TOLERANCE_FRACTION, SPD_TOLERANCE_MW

# Telemetry noise on Base's ALRs is well under 1 MW (mean |RPC - Base Point| was
# 0.01-0.55 MW on 2026-07-20); the one real dispatch that day moved it 6-25 MW.
DEFAULT_DISPATCH_THRESHOLD_MW = 2.0

EVENT_COLUMNS = ["resource", "qse", "start_cpt", "end_cpt", "runs", "max_deviation_mw"]


def dispatch_down(rows: pd.DataFrame, threshold_mw: float = DEFAULT_DISPATCH_THRESHOLD_MW) -> pd.Series:
    """True where SCED's Base Point is more than `threshold_mw` below the
    resource's real power consumption, i.e. SCED asked it to consume less."""
    return rows["deviation_mw"] > threshold_mw


def dispatch_down_events(
    rows: pd.DataFrame, threshold_mw: float = DEFAULT_DISPATCH_THRESHOLD_MW
) -> pd.DataFrame:
    """Consecutive dispatched-down SCED runs of one resource, one row per event.

    `start_cpt` and `end_cpt` are the first and last SCED run of the event.
    """
    events = []
    for resource, runs in rows.sort_values("sced_time_utc").groupby("resource", sort=True):
        down = dispatch_down(runs, threshold_mw)
        episode = (down != down.shift()).cumsum()
        for _, event in runs[down].groupby(episode[down]):
            events.append({
                "resource": resource,
                "qse": event["qse"].iloc[0],
                "start_cpt": event["sced_time_cpt"].iloc[0],
                "end_cpt": event["sced_time_cpt"].iloc[-1],
                "runs": len(event),
                "max_deviation_mw": event["deviation_mw"].max(),
            })
    if not events:
        return pd.DataFrame({
            "resource": pd.Series(dtype=object), "qse": pd.Series(dtype=object),
            "start_cpt": pd.Series(dtype=f"datetime64[ns, {CPT}]"),
            "end_cpt": pd.Series(dtype=f"datetime64[ns, {CPT}]"),
            "runs": pd.Series(dtype=int), "max_deviation_mw": pd.Series(dtype=float),
        })
    out = pd.DataFrame(events, columns=EVENT_COLUMNS)
    return out.sort_values(["start_cpt", "resource"]).reset_index(drop=True)


SUMMARY_COLUMNS = [
    "qse", "mean_flexible_mw", "max_ecrs_award_mw", "max_nspin_award_mw",
    "mean_abs_deviation_mw", "dispatch_down_events",
]


def daily_summary(rows: pd.DataFrame, threshold_mw: float = DEFAULT_DISPATCH_THRESHOLD_MW) -> pd.DataFrame:
    """Per (operating_day, resource): mean flexible MW, max ECRS and Non-Spin
    awards, mean |deviation|, and the number of dispatch-down events that started
    that day."""
    by = rows.groupby(["operating_day", "resource"])
    out = pd.DataFrame({
        "qse": by["qse"].first(),
        "mean_flexible_mw": by["flexible_mw"].mean(),
        "max_ecrs_award_mw": by["as_award_ecrs_mw"].max(),
        "max_nspin_award_mw": by["as_award_nspin_mw"].max(),
        "mean_abs_deviation_mw": by["deviation_mw"].apply(lambda s: s.abs().mean()),
    })
    events = dispatch_down_events(rows, threshold_mw)
    starts = rows.drop_duplicates(["resource", "sced_time_cpt"]).set_index(["resource", "sced_time_cpt"])["operating_day"]
    event_days = [starts.loc[(e.resource, e.start_cpt)] for e in events.itertuples()]
    counts = events.assign(operating_day=event_days).groupby(["operating_day", "resource"]).size()
    out["dispatch_down_events"] = counts.reindex(out.index, fill_value=0).astype(int)
    return out[SUMMARY_COLUMNS]


def grade_delivery(
    rows: pd.DataFrame,
    threshold_mw: float = DEFAULT_DISPATCH_THRESHOLD_MW,
    *,
    tolerance_mw: float = SPD_TOLERANCE_MW,
    tolerance_fraction: float = SPD_TOLERANCE_FRACTION,
) -> dict:
    """How closely Real Power Consumption followed Base Point on dispatch-down events.

    For each event, requested MW is the mean Base Point and delivered MW is the
    mean real power consumption. The shortfall is the worst run's consumption
    above that Base Point. An event is within tolerance when the shortfall is
    within the lesser of `tolerance_fraction` of requested MW and `tolerance_mw`,
    the same Set Point Deviation band the simulated shortfall cost uses.
    """
    events: list[dict[str, object]] = []
    within = 0
    worst = 0.0
    for event in dispatch_down_events(rows, threshold_mw).itertuples(index=False):
        start = cast(pd.Timestamp, event.start_cpt)
        end = cast(pd.Timestamp, event.end_cpt)
        span = rows["resource"].eq(event.resource) & rows["sced_time_cpt"].between(start, end)
        runs = rows.loc[span]
        requested = float(runs["base_point_mw"].mean())
        delivered = float(runs["real_power_consumption_mw"].mean())
        shortfall = float(runs["deviation_mw"].max())
        followed = shortfall <= min(tolerance_fraction * requested, tolerance_mw)
        within += int(followed)
        worst = max(worst, shortfall)
        events.append({
            "resource": event.resource,
            "start_cpt": start.isoformat(),
            "end_cpt": end.isoformat(),
            "requested_mw": requested,
            "delivered_mw": delivered,
            "shortfall_mw": shortfall,
            "within_tolerance": followed,
        })
    n = len(events)
    return {
        "events": events,
        "summary": {
            "events": n,
            "within_tolerance": within,
            "share_within_tolerance": within / n if n else 0.0,
            "worst_shortfall_mw": worst,
        },
    }
