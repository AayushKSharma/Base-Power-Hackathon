"""The harness run seam: run(policy, scenario, date range, seed) -> scorecard.

One CPT operating day is the unit of simulation. A date-range run scores each
day independently and combines the per-day results, so it equals the
combination of single-day runs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

from harness.forecast.catalog import DEFAULT_HORIZON
from harness.market import load_intervals
from harness.market.catalog import Q_OK, quality_column, scarce_column
from harness.observation import ForecastQuery, observations
from harness.policy import Policy
from harness.products import LOAD_ZONES, PRODUCTS
from harness.scenario import Scenario
from harness.scorecard import DayResult, ProductTotals, Scorecard

INTERVAL_H = 5 / 60

# The market-dataset loader contract: operating days [start, end] -> interval table.
MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]

SCORECARD_FILE = "scorecard.json"
INTERVALS_FILE = "intervals.parquet"


@dataclass(frozen=True, eq=False)
class RunResult:
    scorecard: Scorecard
    # The per-interval data dump: one row per 5-minute interval and product.
    intervals: pd.DataFrame

    def write(self, out_dir: Path) -> None:
        """Write the scorecard JSON and, next to it, the per-interval Parquet dump."""
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / SCORECARD_FILE).write_text(self.scorecard.to_json())
        self.intervals.to_parquet(out_dir / INTERVALS_FILE, index=False)


def run(
    policy: Policy,
    scenario: Scenario,
    start: dt.date,
    end: dt.date,
    seed: int | None = None,
    *,
    market: MarketLoader | None = None,
    forecasts: ForecastQuery | None = None,
    forecast_horizon: dt.timedelta = DEFAULT_HORIZON,
) -> RunResult:
    """Score `policy` on `scenario` over operating days [start, end].

    `seed` defaults to the scenario's. Market data comes only from `market`,
    which defaults to the market-dataset loader.
    """
    seed = scenario.seed if seed is None else seed
    if seed < 0:
        raise ValueError(f"seed must be a non-negative integer, got {seed}")
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    intervals = (market or load_intervals)(start, end)
    days, dumps = [], []
    for day, rows in intervals.groupby(intervals["operating_day"].dt.date, sort=True):
        result, dump = _run_day(policy, scenario, cast(dt.date, day), rows, forecasts, forecast_horizon)
        days.append(result)
        dumps.append(dump)
    return RunResult(Scorecard(policy.name, scenario.name, seed, tuple(days)),
                     pd.concat(dumps, ignore_index=True))


def _run_day(policy: Policy, scenario: Scenario, day: dt.date,
             rows: pd.DataFrame, forecasts: ForecastQuery | None,
             forecast_horizon: dt.timedelta) -> tuple[DayResult, pd.DataFrame]:
    decisions = [policy.decide(obs) for obs in observations(
        rows, scenario, forecasts=forecasts, forecast_horizon=forecast_horizon)]
    flat = rows.reset_index()
    totals, dumps = {}, []
    for product, sfx in PRODUCTS.items():
        reported = np.array([float(d[product]) for d in decisions])
        award = np.minimum(reported, scenario.products[product].award_limit_mw)
        price = rows[f"rt_mcpc_15m_{sfx}"].to_numpy(dtype=float)
        scored = (rows[quality_column(f"rt_mcpc_15m_{sfx}")] == Q_OK).to_numpy()
        revenue = np.where(scored, award * price * INTERVAL_H, np.nan)
        totals[product] = ProductTotals(
            intervals=len(rows),
            skipped=int((~scored).sum()),
            reported_mw_h=float(reported[scored].sum() * INTERVAL_H),
            award_mw_h=float(award[scored].sum() * INTERVAL_H),
            revenue=float(revenue[scored].sum()),
        )
        dumps.append(pd.DataFrame({
            "interval_start_utc": flat["interval_start_utc"],
            "interval_start_cpt": flat["interval_start_cpt"],
            "operating_day": flat["operating_day"],
            "product": product,
            "reported_mw": reported,
            "award_mw": award,
            "rt_mcpc_5m": flat[f"rt_mcpc_5m_{sfx}"],
            "rt_mcpc_15m": flat[f"rt_mcpc_15m_{sfx}"],
            "scarce": flat[scarce_column(sfx)],
            "scored": scored,
            "revenue": revenue,
            **{f"lz_spp_{z}": flat[f"lz_spp_{z}"] for z in LOAD_ZONES.values()},
        }))
    dump = pd.concat(dumps).sort_values(["interval_start_utc", "product"], kind="stable")
    return DayResult(day, totals), dump.reset_index(drop=True)
