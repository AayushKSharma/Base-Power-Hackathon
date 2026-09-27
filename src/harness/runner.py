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

from harness.fleet import FleetCase, project_interval, simulate_day, step_soc
from harness.forecast.catalog import DEFAULT_HORIZON
from harness.market import load_intervals
from harness.market.catalog import Q_OK, quality_column, scarce_column
from harness.observation import ForecastQuery, observations
from harness.policy import Policy
from harness.products import LOAD_ZONES, PRODUCTS
from harness.rng import RandomStreams
from harness.scenario import (
    ENERGY_SPD,
    IMBALANCE,
    PER_CASE,
    QUANTILE,
    SPD_TOLERANCE_FRACTION,
    SPD_TOLERANCE_MW,
    TYPICAL,
    Scenario,
)
from harness.scorecard import DayResult, FaultCounts, ProductTotals, Scorecard, to_json

INTERVAL_H = 5 / 60
# K above D by less than this (MW) is float noise, not an overstatement.
TOLERANCE_MW = 1e-9

# The market-dataset loader contract: operating days [start, end] -> interval table.
MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]

SCORECARD_FILE = "scorecard.json"
INTERVALS_FILE = "intervals.parquet"
REPORT_FILE = "report.md"


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
    forecaster: Any | None = None,
    price_horizon_hours: int = 24,
    price_quantiles: Sequence[float] = (0.1, 0.5, 0.9),
) -> RunResult:
    """Score `policy` on `scenario` over operating days [start, end].

    `seed` defaults to the scenario's. Market data comes only from `market`,
    which defaults to the market-dataset loader. `homes`, when given, is
    attached to every observation; an external policy receives it only if its
    handshake asks. `forecasts`, when given, fills each observation from `as_of`.
    `forecaster`, when given, fills each observation's forecaster section with
    one quantile trajectory per hour.
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
    close_forecaster = getattr(forecaster, "close", None) if forecaster is not None else None
    try:
        for group, rows in intervals.groupby(intervals["operating_day"].dt.date, sort=True):
            day = cast(dt.date, group)
            cases = simulate_day(scenario, day, pd.DatetimeIndex(rows.index), _stressed(rows), streams)
            deployed = _deployments(scenario, day, rows, streams)
            moving = scenario.deployments.refill_kw > 0 or any(flag.any() for flag in deployed.values())
            if view == TYPICAL:
                # One decision pass. Its faults are the same events on every case.
                seen = next(c for c in cases if c.name == scenario.fleet.quantile_mock.typical)
                if moving:
                    faults, decisions, seen_violations = _evolve(
                        policy, scenario, day, streams, rows, seen, deployed, homes, forecasts, forecast_horizon,
                        forecaster, price_horizon_hours, price_quantiles)
                    plan = [(case, seen, decisions, faults,
                             seen_violations if case is seen else _replay(scenario, case, deployed, decisions))
                            for case in cases]
                else:
                    faults, decisions = _decide(
                        policy, scenario, day, streams, rows, seen, homes, forecasts, forecast_horizon,
                        forecaster, price_horizon_hours, price_quantiles)
                    plan = [(case, seen, decisions, faults, 0) for case in cases]
            elif moving:
                # A fresh pass per case, so a fallback cannot leak across quantiles.
                plan = [_evolved_pass(
                    policy, scenario, day, streams, rows, case, deployed, homes, forecasts, forecast_horizon,
                    forecaster, price_horizon_hours, price_quantiles)
                    for case in cases]
            else:
                plan = [(*_case_pass(
                    policy, scenario, day, streams, rows, case, homes, forecasts, forecast_horizon,
                    forecaster, price_horizon_hours, price_quantiles), 0)
                    for case in cases]
            for case, seen, reported, faults, violations in plan:
                result, dump = _score(scenario, day, rows, case, reported, deployed, faults, violations)
                days.setdefault(case.name, []).append(result)
                observed[case.name] = seen.name
                dumps.append(dump.assign(policy_view=view, observed_case=seen.name))
        return RunResult(
            {case: Scorecard(policy.name, scenario.name, seed, case, view, observed[case], tuple(results),
                             _beliefs(policy))
             for case, results in days.items()},
            pd.concat(dumps, ignore_index=True),
        )
    finally:
        if callable(close):
            close()
        if callable(close_forecaster):
            close_forecaster()


def _case_pass(policy: Policy, scenario: Scenario, day: dt.date, streams: RandomStreams,
               rows: pd.DataFrame, case: FleetCase,
               homes: Sequence[Mapping[str, Any]] | None, forecasts: ForecastQuery | None,
               forecast_horizon: dt.timedelta, forecaster: Any | None,
               price_horizon_hours: int, price_quantiles: Sequence[float],
               ) -> tuple[FleetCase, FleetCase, dict[str, np.ndarray], FaultCounts]:
    faults, decisions = _decide(
        policy, scenario, day, streams, rows, case, homes, forecasts, forecast_horizon,
        forecaster, price_horizon_hours, price_quantiles,
    )
    return case, case, decisions, faults


def _evolved_pass(policy: Policy, scenario: Scenario, day: dt.date, streams: RandomStreams,
                  rows: pd.DataFrame, case: FleetCase,
                  deployed: dict[str, np.ndarray], homes: Sequence[Mapping[str, Any]] | None,
                  forecasts: ForecastQuery | None, forecast_horizon: dt.timedelta,
                  forecaster: Any | None, price_horizon_hours: int, price_quantiles: Sequence[float],
                  ) -> tuple[FleetCase, FleetCase, dict[str, np.ndarray], FaultCounts, int]:
    faults, decisions, violations = _evolve(
        policy, scenario, day, streams, rows, case, deployed, homes, forecasts, forecast_horizon,
        forecaster, price_horizon_hours, price_quantiles)
    return case, case, decisions, faults, violations


def _replay(scenario: Scenario, case: FleetCase, deployed: dict[str, np.ndarray],
            reported: dict[str, np.ndarray]) -> int:
    """Apply another case's reported MW to this case's own SOC. Returns floor breaches."""
    soc = case.home_soc.copy()
    violations = 0
    for i in range(case.home_online.shape[1]):
        project_interval(scenario, case, soc, i)
        soc, breaches = step_soc(scenario, case, soc, i, _delivered(scenario, case, deployed, reported, i))
        violations += breaches
    return violations


def _evolve(policy: Policy, scenario: Scenario, day: dt.date, streams: RandomStreams,
            rows: pd.DataFrame, case: FleetCase,
            deployed: dict[str, np.ndarray], homes: Sequence[Mapping[str, Any]] | None,
            forecasts: ForecastQuery | None, forecast_horizon: dt.timedelta,
            forecaster: Any | None = None, price_horizon_hours: int = 24,
            price_quantiles: Sequence[float] = (0.1, 0.5, 0.9),
            ) -> tuple[FaultCounts, dict[str, np.ndarray], int]:
    """Decide each interval from the SOC at its start, then let the deployment drain it."""
    begin_day = getattr(policy, "begin_day", None)
    if callable(begin_day):
        begin_day()
    _arm(policy, streams, day)
    if forecaster is not None:
        begin_forecast = getattr(forecaster, "begin_day", None)
        if callable(begin_forecast):
            begin_forecast()
    faults_before = _faults(policy)
    hourly = _hourly_forecaster(forecaster, rows, forecasts, forecast_horizon,
                                price_horizon_hours, price_quantiles)
    observed = observations(rows, scenario, case, homes=homes, forecasts=forecasts,
                            forecast_horizon=forecast_horizon, forecaster=hourly)
    n = len(rows)
    reported = {product: np.empty(n) for product in PRODUCTS}
    soc = case.home_soc.copy()
    violations = 0
    for i in range(n):
        project_interval(scenario, case, soc, i)
        decision = policy.decide(next(observed))
        for product in PRODUCTS:
            reported[product][i] = float(decision[product])
        soc, breaches = step_soc(scenario, case, soc, i, _delivered(scenario, case, deployed, reported, i))
        violations += breaches
    return _faults(policy) - faults_before, reported, violations


def _delivered(scenario: Scenario, case: FleetCase, deployed: dict[str, np.ndarray],
               reported: dict[str, np.ndarray], interval: int) -> dict[str, float]:
    """MW actually discharged: the award, when deployed, limited by true D."""
    out = {}
    for product in PRODUCTS:
        if not deployed[product][interval]:
            out[product] = 0.0
            continue
        award = min(float(reported[product][interval]), scenario.products[product].award_limit_mw)
        out[product] = min(award, float(case.deliverable_mw[product][interval]))
    return out


def _stressed(rows: pd.DataFrame) -> np.ndarray:
    """Intervals where failure rates rise: scarce for either product."""
    scarce = [rows[scarce_column(sfx)].fillna(False).to_numpy(dtype=bool) for sfx in PRODUCTS.values()]
    return np.logical_or.reduce(scarce)


def _mw_h(mw: np.ndarray) -> float:
    return float(mw.sum() * INTERVAL_H)


def _decide(policy: Policy, scenario: Scenario, day: dt.date, streams: RandomStreams,
           rows: pd.DataFrame, seen: FleetCase,
           homes: Sequence[Mapping[str, Any]] | None, forecasts: ForecastQuery | None,
           forecast_horizon: dt.timedelta, forecaster: Any | None = None,
           price_horizon_hours: int = 24, price_quantiles: Sequence[float] = (0.1, 0.5, 0.9),
           ) -> tuple[FaultCounts, dict[str, np.ndarray]]:
    """The policy's reported MW per product for each interval, seeing the fleet case `seen`.

    `begin_day`, when the policy has one, drops remembered state before the pass.
    That reset is not a restart. The returned counts are this pass only.
    """
    begin_day = getattr(policy, "begin_day", None)
    if callable(begin_day):
        begin_day()
    _arm(policy, streams, day)
    if forecaster is not None:
        begin_forecast = getattr(forecaster, "begin_day", None)
        if callable(begin_forecast):
            begin_forecast()
    faults_before = _faults(policy)
    hourly = _hourly_forecaster(forecaster, rows, forecasts, forecast_horizon,
                                price_horizon_hours, price_quantiles)
    decisions = [policy.decide(obs) for obs in observations(
        rows, scenario, seen, homes=homes, forecasts=forecasts, forecast_horizon=forecast_horizon,
        forecaster=hourly)]
    reported = {product: np.array([float(d[product]) for d in decisions]) for product in PRODUCTS}
    return _faults(policy) - faults_before, reported


def _hourly_forecaster(forecaster: Any | None, rows: pd.DataFrame, forecasts: ForecastQuery | None,
                       forecast_horizon: dt.timedelta, price_horizon_hours: int,
                       price_quantiles: Sequence[float]):
    if forecaster is None:
        return None
    from harness.forecaster.attach import hourly_forecasts

    return hourly_forecasts(
        forecaster, rows, forecasts, forecast_horizon,
        horizon_hours=price_horizon_hours, quantiles=price_quantiles,
    )


def _arm(policy: Policy, streams: RandomStreams, day: dt.date) -> None:
    """Give the policy this day's generator, when it samples (Monte Carlo)."""
    arm = getattr(policy, "arm", None)
    if callable(arm):
        arm(streams, day)


def _beliefs(policy: Policy) -> dict[str, float] | None:
    """The policy's recorded belief, or None when it does not carry one."""
    beliefs = getattr(policy, "beliefs", None)
    if not isinstance(beliefs, Mapping):
        return None
    return {str(key): float(value) for key, value in beliefs.items()}


def _faults(policy: Policy) -> FaultCounts:
    """Fault counts the policy has recorded, or zero for an in-process policy."""
    faults = getattr(policy, "faults", None)
    return faults if isinstance(faults, FaultCounts) else FaultCounts()


def _deployments(scenario: Scenario, day: dt.date, rows: pd.DataFrame,
                 streams: RandomStreams) -> dict[str, np.ndarray]:
    """Whether each product is deployed at each interval.

    One draw per interval and product, from the "deployments" stream, so the
    calls do not depend on the policy or the fleet case. A scarce interval uses
    the scarce probability; the rest use the calm one. Forced deployments are
    on regardless of the draw.
    """
    n = len(rows)
    draws = streams.generator(day, "deployments").random((n, len(PRODUCTS)))
    starts = pd.DatetimeIndex(rows.index)
    flags = {}
    for j, (product, sfx) in enumerate(PRODUCTS.items()):
        scarce = rows[scarce_column(sfx)].fillna(False).to_numpy(dtype=bool)
        probability = np.where(scarce, scenario.deployments.scarce, scenario.deployments.calm)
        flags[product] = (draws[:, j] < probability) | _forced_on(scenario, product, starts)
    return flags


def _forced_on(scenario: Scenario, product: str, starts: pd.DatetimeIndex) -> np.ndarray:
    on = np.zeros(len(starts), dtype=bool)
    for event in scenario.deployments.forced:
        if event.product != product:
            continue
        end = event.start + pd.Timedelta(minutes=event.minutes)
        on |= (starts >= event.start) & (starts < end)
    return on


def _shortfall_cost(scenario: Scenario, product: str, shortfall: np.ndarray, award: np.ndarray,
                    lam: np.ndarray, lam_ok: np.ndarray, price: np.ndarray, priced: np.ndarray) -> np.ndarray:
    """Dollars of shortfall per interval: the preset, plus compliance per MW short."""
    h = scenario.products[product].duration_h
    scoring = scenario.scoring
    compliance = scoring.compliance_per_mw * shortfall
    if scoring.preset == IMBALANCE:
        return np.where(priced, price * shortfall * INTERVAL_H, 0.0) + compliance
    energy = np.where(lam_ok, lam * shortfall * h, 0.0)
    if scoring.preset == ENERGY_SPD:
        tolerance = np.minimum(SPD_TOLERANCE_FRACTION * award, SPD_TOLERANCE_MW)
        beyond = np.maximum(shortfall - tolerance, 0.0)
        energy = energy + np.where(lam_ok, scoring.spd_per_mwh * beyond * h, 0.0)
    return energy + compliance


def _fold(reported: np.ndarray, award: np.ndarray, deliverable: np.ndarray, oversold: np.ndarray,
         undersold: np.ndarray, revenue: np.ndarray, given_up: np.ndarray, shortfall: np.ndarray,
         cost: np.ndarray, priced: np.ndarray, mask: np.ndarray) -> ProductTotals:
    """Sums and counts on the intervals `mask` selects."""
    dollars = mask & priced
    earned = float(revenue[dollars].sum()) if dollars.any() else 0.0
    regret = float(given_up[dollars].sum()) if dollars.any() else 0.0
    spent = float(cost[mask].sum()) if mask.any() else 0.0
    return ProductTotals(
        intervals=int(mask.sum()),
        skipped=int((mask & ~priced).sum()),
        reported_mw_h=_mw_h(reported[mask]),
        award_mw_h=_mw_h(award[mask]),
        deliverable_mw_h=_mw_h(deliverable[mask]),
        oversold_mw_h=_mw_h(oversold[mask]),
        undersold_mw_h=_mw_h(undersold[mask]),
        overstated=int((reported[mask] > deliverable[mask] + TOLERANCE_MW).sum()),
        revenue=earned,
        revenue_given_up=regret,
        shortfall_mw_h=_mw_h(shortfall[mask]),
        shortfall_cost=spent,
        net=earned - spent,
    )


def _score(scenario: Scenario, day: dt.date, rows: pd.DataFrame, case: FleetCase,
           reported_mw: dict[str, np.ndarray], deployed: dict[str, np.ndarray],
           faults: FaultCounts, violations: int) -> tuple[DayResult, pd.DataFrame]:
    """Score reported MW against the fleet case's true deliverable MW."""
    flat = rows.reset_index()
    totals, calm, scarce, hourly, dumps = {}, {}, {}, {}, []
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
        called = deployed[product]
        shortfall = np.where(called, np.maximum(award - deliverable, 0.0), 0.0)
        zone = LOAD_ZONES[scenario.scoring.load_zone]
        lam = rows[f"lz_spp_{zone}"].to_numpy(dtype=float)
        cost = _shortfall_cost(scenario, product, shortfall, award, lam, np.isfinite(lam), price, priced)
        scarce_mask = rows[scarce_column(sfx)].fillna(False).to_numpy(dtype=bool)
        masks = {
            "all": np.ones(len(rows), dtype=bool),
            "calm": ~scarce_mask,
            "scarce": scarce_mask,
        }
        folded = {name: _fold(reported, award, deliverable, oversold, undersold, revenue, given_up,
                              shortfall, cost, priced, mask)
                  for name, mask in masks.items()}
        totals[product] = folded["all"]
        calm[product] = folded["calm"]
        scarce[product] = folded["scarce"]
        hour = pd.to_datetime(rows["interval_start_cpt"]).dt.floor("h")
        hourly[product] = tuple(float(v) for v in pd.Series(shortfall, index=hour).groupby(level=0, sort=True).max())
        dumps.append(pd.DataFrame({
            "interval_start_utc": flat["interval_start_utc"],
            "interval_start_cpt": flat["interval_start_cpt"],
            "operating_day": flat["operating_day"],
            "fleet_case": case.name,
            "product": product,
            "reported_mw": reported,
            "deliverable_mw": deliverable,
            "award_mw": award,
            "deployed": called,
            "shortfall_mw": shortfall,
            "shortfall_cost": cost,
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
    return DayResult(day, totals, faults, violations, calm, scarce, hourly), dump.reset_index(drop=True)
