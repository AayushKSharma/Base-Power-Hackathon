"""Reference capacity policies and the belief they carry, apart from the scenario.

A belief is the policy's own deployment probabilities, compliance cost and
failure rates. It defaults to the baseline scenario's calm-condition numbers.
The scenario's failure model stays what the scorecard grades against.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from harness.products import PRODUCTS
from harness.rng import RandomStreams

# One stream in the harness generator tree, shared by in-process and external copies.
REFERENCE_STREAM = "reference_policy"

ZONES = ("HOUSTON", "NORTH", "SOUTH", "WEST")
_MCPC_SERIES = {"ECRS": "MCPC_ECRS", "NONSPIN": "MCPC_NSPIN"}
_NONSPIN_NAMES = ("NSPIN", "NONSPIN", "Non-Spin", "NSpin")
_FORECAST_WIDTH = {
    "dam_mcpc": pd.Timedelta(hours=1),
    "dam_spp": pd.Timedelta(hours=1),
    "rtd_mcpc": pd.Timedelta(minutes=5),
    "rtd_lmp": pd.Timedelta(minutes=5),
}


@dataclass(frozen=True)
class Belief:
    """What a reference policy assumes. Not the scenario's true failure model.

    Zone shares are the fleet's capacity mix across load zones. They weight λ.
    They default to the baseline scenario's Houston zone.
    """

    deployment_calm: float = 0.02
    deployment_scarce: float = 0.30
    compliance_per_mw: float = 500.0
    home_dropout_per_h: float = 0.01
    home_dropout_min: float = 60.0
    region_outage_per_h: float = 0.005
    region_outage_min: float = 120.0
    scarcity_stress: float = 4.0
    zone_houston: float = 1.0
    zone_north: float = 0.0
    zone_south: float = 0.0
    zone_west: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "deployment_calm": self.deployment_calm,
            "deployment_scarce": self.deployment_scarce,
            "compliance_per_mw": self.compliance_per_mw,
            "home_dropout_per_h": self.home_dropout_per_h,
            "home_dropout_min": self.home_dropout_min,
            "region_outage_per_h": self.region_outage_per_h,
            "region_outage_min": self.region_outage_min,
            "scarcity_stress": self.scarcity_stress,
            "zone_houston": self.zone_houston,
            "zone_north": self.zone_north,
            "zone_south": self.zone_south,
            "zone_west": self.zone_west,
        }

    def deployment(self, scarce: bool) -> float:
        return self.deployment_scarce if scarce else self.deployment_calm

    def stress(self, scarce: bool) -> float:
        return self.scarcity_stress if scarce else 1.0

    @property
    def zone_shares(self) -> tuple[float, ...]:
        return (self.zone_houston, self.zone_north, self.zone_south, self.zone_west)


def beliefs_from_mapping(raw: Any) -> dict[str, float] | None:
    """A belief mapping as the scorecard records it, or None when it is malformed."""
    if not isinstance(raw, Mapping):
        return None
    known = Belief().as_dict()
    fields: dict[str, float] = {}
    for key, value in raw.items():
        if key not in known or isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        fields[str(key)] = float(value)
    return Belief(**fields).as_dict()


class CorrelatedNewsvendor:
    """Report the capability with the best expected value under joint dropouts.

    Each decision draws `samples` futures of regional outages and home dropouts
    from the harness generator for that day. The stream is `reference_policy`,
    so a rerun with the same scenario, seed and day repeats the same draws.
    """

    def __init__(self, samples: int = 64, belief: Belief | None = None):
        self.samples = samples
        self.belief = belief if belief is not None else Belief()
        self._generator: np.random.Generator | None = None

    @property
    def name(self) -> str:
        return f"correlated_newsvendor(samples={self.samples:g})"

    @property
    def beliefs(self) -> dict[str, float]:
        return self.belief.as_dict()

    def arm(self, streams: RandomStreams, day: dt.date) -> None:
        """Seed this day's draws from the harness generator tree."""
        self._generator = streams.generator(day, REFERENCE_STREAM)

    def decide(self, observation: Mapping[str, Any]) -> dict[str, float]:
        generator = self._generator
        if generator is None:
            generator = np.random.default_rng(0)
            self._generator = generator
        return {product: _correlated_mw(observation, product, self.belief, self.samples, generator)
                for product in PRODUCTS}


def _correlated_mw(observation: Mapping[str, Any], product: str, belief: Belief, samples: int,
                   rng: np.random.Generator) -> float:
    scarce = bool(observation["now"]["scarce"].get(product))
    hours = float(observation["products"][product]["duration_h"])
    price, lam = _prices(observation, product, belief)
    draw = _sample_deliverable(observation, product, belief, scarce, samples, rng)
    return _best_capability(draw, price, belief.deployment(scarce), lam, hours, belief.compliance_per_mw)


def _sample_deliverable(observation: Mapping[str, Any], product: str, belief: Belief, scarce: bool,
                        samples: int, rng: np.random.Generator) -> np.ndarray:
    """One joint sample of regional outages and home dropouts per draw."""
    hours = float(observation["products"][product]["duration_h"])
    stress = belief.stress(scarce)
    home_fail = _window_failure(belief.home_dropout_per_h, hours, stress)
    region_fail = _window_failure(belief.region_outage_per_h, hours, stress)
    total = np.zeros(samples)
    for region in observation["fleet"]["regions"]:
        online = int(region["homes_online"])
        if online <= 0:
            continue
        mw = float(region["capability_kw"][product]) / online / 1000.0
        if mw <= 0:
            continue
        failed = rng.random(samples) < region_fail
        alive = rng.binomial(online, 1.0 - home_fail, size=samples)
        total += np.where(failed, 0.0, alive * mw)
    return total


def _best_capability(draw: np.ndarray, price: float, deployment: float, lam: float, hours: float,
                     cost: float) -> float:
    """The sampled capability with the best expected value, breaking ties low."""
    penalty = deployment * (lam * hours + cost)
    candidates = np.unique(np.concatenate([np.asarray(draw, dtype=float), np.zeros(1)]))
    shortfall = np.maximum(candidates[:, None] - draw[None, :], 0.0).mean(axis=1)
    value = price * candidates - penalty * shortfall
    best = float(value.max())
    tied = candidates[value >= best - 1e-9]
    return float(tied.min())


class ReliabilityTarget:
    """Report the largest K with P(D < K) <= epsilon under this policy's belief.

    D includes regional outages and home dropouts from the belief, stressed
    when the interval is scarce.
    """

    def __init__(self, epsilon: float = 0.05, belief: Belief | None = None):
        self.epsilon = epsilon
        self.belief = belief if belief is not None else Belief()

    @property
    def name(self) -> str:
        return f"reliability_target(epsilon={self.epsilon:g})"

    @property
    def beliefs(self) -> dict[str, float]:
        return self.belief.as_dict()

    def decide(self, observation: Mapping[str, Any]) -> dict[str, float]:
        return {product: _reliability_mw(observation, product, self.belief, self.epsilon)
                for product in PRODUCTS}


def _reliability_mw(observation: Mapping[str, Any], product: str, belief: Belief, epsilon: float) -> float:
    scarce = bool(observation["now"]["scarce"].get(product))
    hours = float(observation["products"][product]["duration_h"])
    home_fail = _window_failure(belief.home_dropout_per_h, hours, belief.stress(scarce))
    region_fail = _window_failure(belief.region_outage_per_h, hours, belief.stress(scarce))
    pmf, mw = _count_pmf(observation, product, 1.0 - home_fail, region_fail)
    if mw <= 0:
        return 0.0
    return _chance_count(pmf, epsilon) * mw


def _chance_count(pmf: np.ndarray, epsilon: float) -> int:
    """Largest home count k with P(count < k) <= epsilon."""
    cdf = np.cumsum(pmf)
    best = 0
    for k in range(len(pmf)):
        before = 0.0 if k == 0 else float(cdf[k - 1])
        if before <= epsilon + 1e-12:
            best = k
        else:
            break
    return best


class IndependentNewsvendor:
    """α-quantile of deliverable MW if homes drop out independently.

    α = p / (d · (λ · h + c)). Regional outages are ignored: that is the point
    of the independent policy, next to the correlated Monte Carlo one.
    """

    def __init__(self, belief: Belief | None = None):
        self.belief = belief if belief is not None else Belief()

    @property
    def name(self) -> str:
        return "independent_newsvendor"

    @property
    def beliefs(self) -> dict[str, float]:
        return self.belief.as_dict()

    def decide(self, observation: Mapping[str, Any]) -> dict[str, float]:
        return {product: _independent_mw(observation, product, self.belief) for product in PRODUCTS}


def _independent_mw(observation: Mapping[str, Any], product: str, belief: Belief) -> float:
    scarce = bool(observation["now"]["scarce"].get(product))
    hours = float(observation["products"][product]["duration_h"])
    price, lam = _prices(observation, product, belief)
    alpha = _critical_alpha(price, belief.deployment(scarce), lam, hours, belief.compliance_per_mw)
    failure = _window_failure(belief.home_dropout_per_h, hours, belief.stress(scarce))
    count, mw = _independent_count(observation, product, 1.0 - failure, alpha)
    return count * mw


def _critical_alpha(price: float, deployment: float, lam: float, hours: float, cost: float) -> float:
    """The newsvendor critical fractile. Infinite when overstating is free."""
    denom = deployment * (lam * hours + cost)
    if denom <= 0:
        return math.inf
    return price / denom


def _window_failure(per_hour: float, hours: float, stress: float) -> float:
    """Chance a unit fails to cover the whole window, if any event during it
    knocks it out and the per-hour rate is stressed in scarce intervals.

    Any positive-length event that starts inside the window overlaps it, so the
    survival chance is (1 - stressed rate) ** hours. Rates above 1 are capped:
    a probability cannot exceed one.
    """
    rate = min(1.0, max(0.0, per_hour * stress))
    if hours <= 0 or rate <= 0:
        return 0.0
    if rate >= 1:
        return 1.0
    return 1.0 - (1.0 - rate) ** hours


def _independent_count(observation: Mapping[str, Any], product: str, survive: float, alpha: float,
                       ) -> tuple[int, float]:
    """Units to count on, and MW per unit, under independent survival.

    Regional outages are left out. A unit is one home when every online home has
    the same MW; otherwise it is the common step those sizes sit on.
    """
    pmf, step = _count_pmf(observation, product, survive, region_fail=0.0)
    total = len(pmf) - 1
    if step <= 0:
        return 0, 0.0
    if alpha <= 0:
        return 0, step
    if alpha >= 1:
        return total, step
    count = int(np.searchsorted(np.cumsum(pmf), alpha, side="left"))
    return min(count, total), step


def _count_pmf(observation: Mapping[str, Any], product: str, survive: float, region_fail: float,
               ) -> tuple[np.ndarray, float]:
    """Distribution of surviving units, and MW per unit.

    Each region keeps its own megawatts per online home. When those sizes differ
    they are written as integer multiples of one common step, so a 16 kW home
    is two 8 kW steps and the quantile is still exact.
    """
    pieces: list[tuple[int, float]] = []
    for region in observation["fleet"]["regions"]:
        online = int(region["homes_online"])
        if online <= 0:
            continue
        mw = float(region["capability_kw"][product]) / online / 1000.0
        if mw <= 0:
            continue
        pieces.append((online, mw))
    if not pieces:
        return np.array([1.0]), 0.0
    step = _common_step([mw for _, mw in pieces])
    dist = np.array([1.0])
    for online, mw in pieces:
        units = max(1, int(round(mw / step)))
        homes = _binomial_pmf(online, survive)
        expanded = np.zeros(online * units + 1)
        for k, prob in enumerate(homes):
            expanded[k * units] = prob
        if region_fail > 0:
            expanded *= (1.0 - region_fail)
            expanded[0] += region_fail
        dist = np.convolve(dist, expanded)
    return dist, step


def _common_step(megawatts: list[float]) -> float:
    """Largest step that puts every per-home MW on an integer multiple of it."""
    step = min(megawatts)
    for _ in range(12):
        if all(_on_step(mw, step) for mw in megawatts):
            return step
        step /= 2.0
    return step


def _on_step(mw: float, step: float) -> bool:
    units = round(mw / step)
    return units >= 1 and abs(mw - units * step) <= 1e-9 * mw


def _binomial_pmf(n: int, p: float) -> np.ndarray:
    """Probability of 0..n successes. Sums to one."""
    out = np.zeros(n + 1)
    if n == 0 or p <= 0:
        out[0] = 1.0
        return out
    if p >= 1:
        out[n] = 1.0
        return out
    log_p = math.log(p)
    log_q = math.log(1.0 - p)
    logs = np.empty(n + 1)
    logs[0] = n * log_q
    for k in range(n):
        logs[k + 1] = logs[k] + math.log(n - k) - math.log(k + 1) + log_p - log_q
    weights = np.exp(logs - np.max(logs))
    out[:] = weights / weights.sum()
    return out


def _prices(observation: Mapping[str, Any], product: str, belief: Belief) -> tuple[float, float]:
    """(RT MCPC, capacity-weighted load-zone price).

    The forecaster's median, at the first step, wins when that section is
    present. Else a forecast vintage posted at or before the decision and valid
    for it. Else the current row. A missing price is zero.
    """
    forecaster = observation.get("forecaster") or {}
    forecasts = observation.get("forecasts") or {}
    moment = pd.Timestamp(observation["now"]["interval_start_utc"]).tz_convert("UTC")
    price = _forecaster_point(forecaster, _MCPC_SERIES[product])
    lam = _forecaster_lambda(forecaster, belief)
    if price is None:
        price = _forecast_quote(forecasts, moment, ("rtd_mcpc", "dam_mcpc"), _mcpc_names(product))
    if lam is None:
        lam = _forecast_lambda(forecasts, moment, belief)
    if price is None:
        mcpc = observation["now"]["rt_mcpc"].get(product)
        price = 0.0 if mcpc is None else float(mcpc)
    if lam is None:
        lam = _zone_price(observation["now"]["lz_price"], belief)
    return price, 0.0 if lam is None else lam


def _mcpc_names(product: str) -> tuple[str, ...]:
    return ("ECRS",) if product == "ECRS" else _NONSPIN_NAMES


def _forecast_lambda(forecasts: Mapping[str, Any], moment: pd.Timestamp, belief: Belief) -> float | None:
    quoted = {
        zone: _forecast_quote(forecasts, moment, ("rtd_lmp", "dam_spp"), (f"LZ_{zone}",))
        for zone in ZONES
    }
    if all(price is None for price in quoted.values()):
        return None
    return _zone_price(quoted, belief)


def _forecast_quote(forecasts: Mapping[str, Any], moment: pd.Timestamp, sources: tuple[str, ...],
                    names: tuple[str, ...]) -> float | None:
    """Latest value posted at or before `moment` and valid for it.

    A row posted after the decision is skipped before its value is read.
    """
    chosen_at: pd.Timestamp | None = None
    chosen: float | None = None
    for source in sources:
        width = _FORECAST_WIDTH[source]
        for row in forecasts.get(source, []):
            if str(row.get("series", "")) not in names:
                continue
            posted = _utc(row.get("posted_time"))
            valid = _utc(row.get("valid_time"))
            if posted is None or valid is None or posted > moment:
                continue
            if not (valid <= moment < valid + width):
                continue
            if chosen_at is None or posted >= chosen_at:
                chosen_at = posted
                chosen = float(row["value"])
    return chosen


def _utc(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return None
    return stamp.tz_convert("UTC")


def _forecaster_point(forecaster: Mapping[str, Any], series: str) -> float | None:
    """First step of the quantile closest to the median, or None if unusable."""
    block = forecaster.get("series")
    if not isinstance(block, Mapping) or series not in block:
        return None
    spec = block[series]
    if not isinstance(spec, Mapping):
        return None
    quantiles = spec.get("quantiles") or []
    values = spec.get("values") or []
    if not quantiles or not values:
        return None
    index = min(range(len(quantiles)), key=lambda i: abs(float(quantiles[i]) - 0.5))
    if index >= len(values) or not values[index]:
        return None
    return float(values[index][0])


def _forecaster_lambda(forecaster: Mapping[str, Any], belief: Belief) -> float | None:
    quoted = {zone: _forecaster_point(forecaster, f"LZ_{zone}") for zone in ZONES}
    if all(price is None for price in quoted.values()):
        return None
    return _zone_price(quoted, belief)


def _zone_price(prices: Mapping[str, Any], belief: Belief) -> float | None:
    """Capacity-weighted mean of the zones the belief puts the fleet in."""
    total = 0.0
    weight = 0.0
    seen = False
    for zone, share in zip(ZONES, belief.zone_shares):
        if share <= 0:
            continue
        price = prices.get(zone)
        if price is None:
            continue
        seen = True
        total += share * float(price)
        weight += share
    if not seen or weight <= 0:
        return None
    return total / weight
