"""The harness run seam: run(policy, scenario, date range, seed) -> scorecards.

One CPT operating day is the unit of simulation. A date-range run scores each
day independently and combines the per-day results, so it equals the
combination of single-day runs.

A run yields one scorecard per fleet case: one per quantile mock (P10..P90) in
quantile mode, or one in stochastic mode. Every case is scored on the same
market data, and the random draws depend only on (scenario, seed, day, stream),
never on the policy.

What the policy observes depends on the view (see harness.scenario.QuantileMock):
- TYPICAL (quantile mode's default): one decision per interval, from the
  typical quantile's fleet, scored against every quantile's true D.
- PER_CASE (and stochastic mode): one decision per interval for each case,
  from that case's own observed fleet.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from harness.fleet import FleetCase, simulate_day
from harness.forecast.catalog import DEFAULT_HORIZON
from harness.market import load_intervals
from harness.market.catalog import Q_OK, quality_column, scarce_column
from harness.observation import ForecastQuery, observations
from harness.policy import Policy
from harness.products import LOAD_ZONES, PRODUCTS
from harness.rng import RandomStreams
from harness.scenario import PER_CASE, QUANTILE, TYPICAL, Scenario
from harness.scorecard import DayResult, FaultCounts, ProductTotals, Scorecard, to_json

INTERVAL_H = 5 / 60
# K above D by less than this (MW) is float noise, not an overstatement.
TOLERANCE_MW = 1e-9

# The market-dataset loader contract: operating days [start, end] -> interval table.
MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]

SCORECARD_FILE = "scorecard.json"
INTERVALS_FILE = "intervals.parquet"


@dataclass(frozen=True, eq=False)
class RunResult:
    # One scorecard per fleet case ("P10" ... "P90", or "stochastic"), in order.
    scorecards: dict[str, Scorecard]
    # The per-interval data dump: one row per 5-minute interval, fleet case and product.
    intervals: pd.DataFrame

    def write(self, out_dir: Path) -> None:
        """Write the scorecard JSON and, next to it, the per-interval Parquet dump."""
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / SCORECARD_FILE).write_text(to_json(list(self.scorecards.values())))
        self.intervals.to_parquet(out_dir / INTERVALS_FILE, index=False)


def run(
    policy: Policy,
    scenario: Scenario,
    start: dt.date,
    end: dt.date,
    seed: int | None = None,
    *,
    market: MarketLoader | None = None,
    homes: Sequence[Mapping[str, Any]] | None = None,
    forecasts: ForecastQuery | None = None,
    forecast_horizon: dt.timedelta = DEFAULT_HORIZON,
) -> RunResult:
    """Score `policy` on `scenario` over operating days [start, end].

    `seed` defaults to the scenario's. Market data comes only from `market`,
    which defaults to the market-dataset loader. `homes`, when given, is
    attached to every observation; an external policy receives it only if its
    handshake asks. `forecasts`, when given, fills each observation from `as_of`.
    """
    seed = scenario.seed if seed is None else seed
    streams = RandomStreams(scenario.name, seed)
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    intervals = (market or load_intervals)(start, end)
    view = scenario.fleet.quantile_mock.policy_view if scenario.fleet.state == QUANTILE else PER_CASE
    days: dict[str, list[DayResult]] = {}
    observed: dict[str, str] = {}
    dumps = []
    close = getattr(policy, "close", None)
    try:
        for group, rows in intervals.groupby(intervals["operating_day"].dt.date, sort=True):
            day = cast(dt.date, group)
            cases = simulate_day(scenario, day, pd.DatetimeIndex(rows.index), _stressed(rows), streams)
            if view == TYPICAL:
                # One decision pass. Its faults are the same events on every case.
                seen = next(c for c in cases if c.name == scenario.fleet.quantile_mock.typical)
                faults, decisions = _decide(policy, scenario, rows, seen, homes, forecasts, forecast_horizon)
                plan = [(case, seen, decisions, faults) for case in cases]
            else:
                # A fresh pass per case, so a fallback cannot leak across quantiles.
                plan = [_case_pass(policy, scenario, rows, case, homes, forecasts, forecast_horizon)
                        for case in cases]
            for case, seen, reported, faults in plan:
                result, dump = _score(scenario, day, rows, case, reported, faults)
                days.setdefault(case.name, []).append(result)
                observed[case.name] = seen.name
                dumps.append(dump.assign(policy_view=view, observed_case=seen.name))
        return RunResult(
            {case: Scorecard(policy.name, scenario.name, seed, case, view, observed[case], tuple(results))
             for case, results in days.items()},
            pd.concat(dumps, ignore_index=True),
        )
    finally:
        if callable(close):
            close()


def _case_pass(policy: Policy, scenario: Scenario, rows: pd.DataFrame, case: FleetCase,
               homes: Sequence[Mapping[str, Any]] | None, forecasts: ForecastQuery | None,
               forecast_horizon: dt.timedelta,
               ) -> tuple[FleetCase, FleetCase, dict[str, np.ndarray], FaultCounts]:
    faults, decisions = _decide(policy, scenario, rows, case, homes, forecasts, forecast_horizon)
    return case, case, decisions, faults


def _stressed(rows: pd.DataFrame) -> np.ndarray:
    """Intervals where failure rates rise: scarce for either product."""
    scarce = [rows[scarce_column(sfx)].fillna(False).to_numpy(dtype=bool) for sfx in PRODUCTS.values()]
    return np.logical_or.reduce(scarce)


def _mw_h(mw: np.ndarray) -> float:
    return float(mw.sum() * INTERVAL_H)


def _decide(policy: Policy, scenario: Scenario, rows: pd.DataFrame, seen: FleetCase,
           homes: Sequence[Mapping[str, Any]] | None, forecasts: ForecastQuery | None,
           forecast_horizon: dt.timedelta) -> tuple[FaultCounts, dict[str, np.ndarray]]:
    """The policy's reported MW per product for each interval, seeing the fleet case `seen`.

    `begin_day`, when the policy has one, drops remembered state before the pass.
    That reset is not a restart. The returned counts are this pass only.
    """
    begin_day = getattr(policy, "begin_day", None)
    if callable(begin_day):
        begin_day()
    faults_before = _faults(policy)
    decisions = [policy.decide(obs) for obs in observations(
        rows, scenario, seen, homes=homes, forecasts=forecasts, forecast_horizon=forecast_horizon)]
    reported = {product: np.array([float(d[product]) for d in decisions]) for product in PRODUCTS}
    return _faults(policy) - faults_before, reported


def _faults(policy: Policy) -> FaultCounts:
    """Fault counts the policy has recorded, or zero for an in-process policy."""
    faults = getattr(policy, "faults", None)
    return faults if isinstance(faults, FaultCounts) else FaultCounts()


def _score(scenario: Scenario, day: dt.date, rows: pd.DataFrame, case: FleetCase,
           reported_mw: dict[str, np.ndarray], faults: FaultCounts) -> tuple[DayResult, pd.DataFrame]:
    """Score reported MW against the fleet case's true deliverable MW."""
    flat = rows.reset_index()
    totals, dumps = {}, []
    for product, sfx in PRODUCTS.items():
        limit = scenario.products[product].award_limit_mw
        reported = reported_mw[product]
        deliverable = case.deliverable_mw[product]
        award = np.minimum(reported, limit)
        oversold = np.maximum(reported - deliverable, 0)
        undersold = np.maximum(deliverable - reported, 0)
        price = rows[f"rt_mcpc_15m_{sfx}"].to_numpy(dtype=float)
        priced = (rows[quality_column(f"rt_mcpc_15m_{sfx}")] == Q_OK).to_numpy()
        revenue = np.where(priced, award * price * INTERVAL_H, np.nan)
        given_up = np.where(priced, np.maximum(np.minimum(deliverable, limit) - award, 0) * price * INTERVAL_H,
                            np.nan)
        totals[product] = ProductTotals(
            intervals=len(rows),
            skipped=int((~priced).sum()),
            reported_mw_h=_mw_h(reported),
            award_mw_h=_mw_h(award),
            deliverable_mw_h=_mw_h(deliverable),
            oversold_mw_h=_mw_h(oversold),
            undersold_mw_h=_mw_h(undersold),
            overstated=int((reported > deliverable + TOLERANCE_MW).sum()),
            revenue=float(revenue[priced].sum()),
            revenue_given_up=float(given_up[priced].sum()),
        )
        dumps.append(pd.DataFrame({
            "interval_start_utc": flat["interval_start_utc"],
            "interval_start_cpt": flat["interval_start_cpt"],
            "operating_day": flat["operating_day"],
            "fleet_case": case.name,
            "product": product,
            "reported_mw": reported,
            "deliverable_mw": deliverable,
            "award_mw": award,
            "oversold_mw": oversold,
            "undersold_mw": undersold,
            "rt_mcpc_5m": flat[f"rt_mcpc_5m_{sfx}"],
            "rt_mcpc_15m": flat[f"rt_mcpc_15m_{sfx}"],
            "scarce": flat[scarce_column(sfx)],
            "priced": priced,
            "revenue": revenue,
            "revenue_given_up": given_up,
            **{f"lz_spp_{z}": flat[f"lz_spp_{z}"] for z in LOAD_ZONES.values()},
        }))
    dump = pd.concat(dumps).sort_values(["interval_start_utc", "product"], kind="stable")
    return DayResult(day, totals, faults), dump.reset_index(drop=True)
