"""Fleet state for one operating day: what the policy observes, and what the
fleet can truly deliver.

Both fleet-state modes come down to the same thing: which homes are available
at each 5-minute step, plus each home's SOC.

- QUANTILE: a mock gives the share of homes available at P10..P90 for each CPT
  month and hour. Homes are ranked once per day, so a higher quantile always
  has a superset of a lower one's homes. Each quantile is its own fleet case,
  and nothing fails mid-window.
- STOCHASTIC: homes drop out (lose telemetry and control) and regions lose
  grid power (their homes go into backup mode), at random with rates that rise
  in scarce intervals, plus any forced regional outages. One fleet case.

At the start of each interval the policy observes, per region:
- homes online;
- homes stale: in a dropout whose telemetry is older than the stale threshold;
- homes in backup mode.

It also sees the online homes' energy above the floor, their inverter kW, and
their capability per product: the sum of min(inverter kW, energy above floor /
duration) over each online home. A home that dropped out less than the stale
threshold ago still looks online.

True deliverable MW (D) for a product sums the same per-home capability, but
only over homes available for the whole product duration from the interval's
start. Windows that run past midnight use the same day's draws, so each day
stays independent.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from harness.market.catalog import CPT
from harness.rng import RandomStreams
from harness.scenario import QUANTILE, QUANTILES, STOCHASTIC, Failures, Scenario

STEP_S = 300  # one 5-minute interval
# A duration within this many steps of a whole number of steps is that many steps.
STEP_ROUNDING = 1e-9


@dataclass(frozen=True, eq=False)
class FleetCase:
    """One fleet state (a quantile mock, or the stochastic model) over one day."""

    name: str  # "P10" ... "P90", or "stochastic"
    # Observed at each interval start, per region; arrays of shape (intervals, regions).
    homes: np.ndarray
    homes_online: np.ndarray
    homes_stale: np.ndarray
    homes_backup: np.ndarray
    energy_above_floor_kwh: np.ndarray
    inverter_kw: np.ndarray
    capability_kw: dict[str, np.ndarray]  # per product
    # True deliverable MW per product, shape (intervals,).
    deliverable_mw: dict[str, np.ndarray]
    # Home-level state for draining SOC. The scorer steps a copy of `home_soc`.
    home_soc: np.ndarray  # (homes,) starting SOC
    home_online: np.ndarray  # (homes, intervals)
    sustains: dict[str, np.ndarray]  # product -> homes that can hold the window from each interval
    region_of: np.ndarray  # (homes,) region id
    home_backup: np.ndarray  # (homes, intervals) in backup mode at the interval's start
    home_stale: np.ndarray  # (homes, intervals) telemetry already older than the stale threshold


def simulate_day(scenario: Scenario, day: dt.date, starts_utc: pd.DatetimeIndex,
                 scarce: np.ndarray, streams: RandomStreams) -> list[FleetCase]:
    """The fleet cases for one day's intervals (`starts_utc`, 5 minutes apart).

    `scarce` flags the intervals where failure rates are stressed. Random draws
    come only from `streams`, keyed by day, so every policy faces the same ones.
    """
    fleet = scenario.fleet
    n = len(starts_utc)
    window_steps = {p: math.ceil(r.duration_h * 3600 / STEP_S - STEP_ROUNDING)
                    for p, r in scenario.products.items()}
    steps = n + max(window_steps.values())  # the day, plus the longest window past its end
    by_region = np.eye(fleet.regions)[np.arange(fleet.homes) % fleet.regions]  # (homes, regions)

    soc = fleet.soc.draw(fleet.homes, streams.generator(day, "soc"))
    energy_kwh = np.maximum(soc - fleet.backup_floor, 0) * fleet.battery_kwh
    home_kw = {p: np.minimum(fleet.inverter_kw, energy_kwh / r.duration_h) for p, r in scenario.products.items()}

    def case(name: str, available: np.ndarray, online: np.ndarray,
             stale: np.ndarray, backup: np.ndarray) -> FleetCase:
        """Summaries of one availability timeline: `available` (homes, steps) and
        the observed `online`, `stale` and `backup` (homes, intervals)."""
        seen = online.T.astype(float)  # (intervals, homes)
        still_available = _steps_still_available(available)[:, :n]
        return FleetCase(
            name=name,
            homes=np.broadcast_to(by_region.sum(axis=0), (n, fleet.regions)),
            homes_online=seen @ by_region,
            homes_stale=stale.T.astype(float) @ by_region,
            homes_backup=backup.T.astype(float) @ by_region,
            energy_above_floor_kwh=(seen * energy_kwh) @ by_region,
            inverter_kw=(seen * fleet.inverter_kw) @ by_region,
            capability_kw={p: (seen * kw) @ by_region for p, kw in home_kw.items()},
            deliverable_mw={p: home_kw[p] @ (still_available >= w) / 1000 for p, w in window_steps.items()},
            home_soc=soc.copy(),
            home_online=online,
            sustains={p: still_available >= w for p, w in window_steps.items()},
            region_of=np.arange(fleet.homes) % fleet.regions,
            home_backup=np.array(backup, dtype=bool, copy=True),
            home_stale=np.array(stale, dtype=bool, copy=True),
        )

    step_starts = starts_utc[0] + pd.to_timedelta(np.arange(steps) * STEP_S, unit="s")
    nobody = np.zeros((fleet.homes, n), dtype=bool)
    if fleet.state == QUANTILE:
        local = step_starts.tz_convert(CPT)
        month, hour = local.month.to_numpy(), local.hour.to_numpy()
        rank = np.argsort(streams.generator(day, "availability").permutation(fleet.homes))
        cases = []
        for q in QUANTILES:
            count = np.rint(fleet.quantile_mock.share(q, month, hour) * fleet.homes)
            available = rank[:, None] < count[None, :]
            cases.append(case(q, available, available[:, :n], nobody, nobody))
        return cases

    failures = scenario.failures
    stress = np.where(np.concatenate([scarce, np.zeros(steps - n, dtype=bool)]),
                      failures.scarcity_stress, 1.0)
    dropouts = _random_events(streams.generator(day, "home_dropouts"), fleet.homes, steps, n,
                              _step_chance(failures.home_dropout_per_h) * stress, failures.home_dropout_min * 60)
    outages = _random_events(streams.generator(day, "region_outages"), fleet.regions, steps, n,
                             _step_chance(failures.region_outage_per_h) * stress, failures.region_outage_min * 60)
    _force_outages(failures, step_starts, outages)

    region = np.arange(fleet.homes) % fleet.regions
    available = ~dropouts.busy & ~outages.busy[region]
    stale = dropouts.active & (dropouts.age_s > fleet.telemetry_stale_s)
    backup = ~stale & outages.active[region]
    return [case(STOCHASTIC, available, ~stale & ~backup, stale, backup)]


@dataclass(frozen=True, eq=False)
class _Events:
    """Events (dropouts or outages) on some units over a day's steps."""

    busy: np.ndarray  # (units, steps): an event overlaps the step
    active: np.ndarray  # (units, intervals): an event is in progress at the interval's start
    age_s: np.ndarray  # (units, intervals): seconds since that event began


def _step_chance(per_hour: float) -> float:
    """Chance per 5-minute step of an event with the given chance per hour."""
    return 1 - (1 - per_hour) ** (STEP_S / 3600)


def _random_events(rng: np.random.Generator, units: int, steps: int, intervals: int,
                   chance: np.ndarray, duration_s: float) -> _Events:
    """A unit not already in an event starts one in step j with probability
    chance[j], at a uniform time within the step, lasting duration_s."""
    draws, offsets = rng.random((units, steps)), rng.random((units, steps))
    start, end = np.full(units, -np.inf), np.full(units, -np.inf)
    events = _Events(np.zeros((units, steps), dtype=bool), np.zeros((units, intervals), dtype=bool),
                     np.zeros((units, intervals)))
    for j in range(steps):
        t0 = j * STEP_S
        if j < intervals:
            on = (start <= t0) & (t0 < end)
            events.active[:, j], events.age_s[:, j] = on, np.where(on, t0 - start, 0.0)
        new = (end <= t0) & (draws[:, j] < chance[j])
        start = np.where(new, t0 + offsets[:, j] * STEP_S, start)
        end = np.where(new, start + duration_s, end)
        events.busy[:, j] = (start < t0 + STEP_S) & (end > t0)
    return events


def _force_outages(failures: Failures, step_starts: pd.DatetimeIndex, outages: _Events) -> None:
    """Add the scenario's forced regional outages to `outages`."""
    t0 = np.arange(len(step_starts)) * STEP_S  # seconds from the first step
    intervals = outages.active.shape[1]
    for outage in failures.forced_region_outages:
        begin = (outage.start - step_starts[0]).total_seconds()
        end = begin + outage.minutes * 60
        outages.busy[outage.region] |= (begin < t0 + STEP_S) & (end > t0)
        outages.active[outage.region] |= ((begin <= t0) & (t0 < end))[:intervals]


def project_interval(scenario: Scenario, case: FleetCase, soc: np.ndarray, interval: int) -> None:
    """Rewrite one interval's observed energy, capability and deliverable MW from `soc`.

    The products match `simulate_day`: online homes times per-home kW, then the
    region one-hot. A matching summation order keeps an external policy, which
    sees the observation as JSON, on the same floats as an in-process policy.
    """
    fleet = scenario.fleet
    energy = np.maximum(soc - fleet.backup_floor, 0.0) * fleet.battery_kwh
    seen = case.home_online[:, interval].astype(float)
    by_region = np.eye(fleet.regions)[case.region_of]
    case.energy_above_floor_kwh[interval] = (seen * energy) @ by_region
    for product, rules in scenario.products.items():
        kw = np.minimum(fleet.inverter_kw, energy / rules.duration_h)
        case.capability_kw[product][interval] = (seen * kw) @ by_region
        case.deliverable_mw[product][interval] = kw @ case.sustains[product][:, interval] / 1000


def step_soc(scenario: Scenario, case: FleetCase, soc: np.ndarray, interval: int,
             delivered_mw: Mapping[str, float]) -> tuple[np.ndarray, int]:
    """Discharge `delivered_mw` and, in an interval that delivers nothing, refill.

    Discharge is shared across the homes that can sustain each product, in
    proportion to their kW, and clamped so SOC does not fall through the backup
    floor. The returned count is homes whose requested discharge exceeded the
    energy above the floor. A clamp makes that count zero.
    """
    fleet = scenario.fleet
    soc = np.array(soc, dtype=float, copy=True)
    above = np.maximum(soc - fleet.backup_floor, 0.0) * fleet.battery_kwh
    hours = STEP_S / 3600
    requested = np.zeros(fleet.homes)
    for product, mw in delivered_mw.items():
        if mw <= 0:
            continue
        kw = np.minimum(fleet.inverter_kw, above / scenario.products[product].duration_h)
        weights = np.where(case.sustains[product][:, interval], kw, 0.0)
        total = float(weights.sum())
        if total <= 0:
            continue
        requested += mw * 1000.0 * hours * weights / total
    breaches = int(np.count_nonzero(requested > above + 1e-6))
    requested = np.minimum(requested, above)
    soc = np.where(soc >= fleet.backup_floor, fleet.backup_floor + (above - requested) / fleet.battery_kwh, soc)
    if not any(mw > 0 for mw in delivered_mw.values()) and scenario.deployments.refill_kw > 0:
        room = np.maximum(1.0 - soc, 0.0) * fleet.battery_kwh
        add = np.minimum(scenario.deployments.refill_kw * hours, room)
        soc = soc + np.where(case.home_online[:, interval], add / fleet.battery_kwh, 0.0)
    return np.minimum(soc, 1.0), breaches


def _steps_still_available(available: np.ndarray) -> np.ndarray:
    """For each home and step, how many consecutive steps it stays available from there."""
    out = np.zeros(available.shape, dtype=np.int32)
    following = np.zeros(available.shape[0], dtype=np.int32)
    for j in range(available.shape[1] - 1, -1, -1):
        following = np.where(available[:, j], following + 1, 0)
        out[:, j] = following
    return out
