"""Reference price forecasters. The oracle is the only one that looks ahead.

Point forecasts (persistence, DAM, RTD) put the same number on every quantile.
The net-load regression spreads quantiles by the in-sample residual, using a
normal quantile. When the fit is perfect the spread is zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from harness.forecaster.features import NSPIN_NAMES, net_load_at
from harness.forecaster.grade import realized_price
from harness.protocol import PRICE_SERIES

_NORMAL = NormalDist()
_ZONES = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST")
_MCPC = {"MCPC_ECRS": ("ECRS",), "MCPC_NSPIN": NSPIN_NAMES}


def persistence() -> Persistence:
    return Persistence()


def dam_as_forecast() -> DamAsForecast:
    return DamAsForecast()


def rtd_indicative() -> RtdIndicative:
    return RtdIndicative()


def net_load() -> NetLoad:
    return NetLoad()


def oracle(intervals: pd.DataFrame) -> Oracle:
    """Realized prices, labelled look-ahead. For the value of a perfect forecast."""
    return Oracle(intervals)


class Persistence:
    """Repeat the latest realized price of each series."""

    name = "persistence"
    look_ahead = False

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        last = _last_prices(observation)
        return _point(observation, horizon_hours, quantiles,
                      {series: [last[series]] * horizon_hours for series in PRICE_SERIES})


class DamAsForecast:
    """Day-ahead prices as the real-time forecast. Hours without a DAM price persist."""

    name = "dam"
    look_ahead = False

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        issued = _issued(observation)
        last = _last_prices(observation)
        hours: dict[str, list[float]] = {series: [] for series in PRICE_SERIES}
        for step in range(horizon_hours):
            quoted = _dam_prices(observation.get("forecasts", {}), issued + pd.Timedelta(hours=step))
            for series in PRICE_SERIES:
                price = quoted.get(series)
                hours[series].append(last[series] if price is None else price)
        return _point(observation, horizon_hours, quantiles, hours)


class RtdIndicative:
    """RTD's indicative price for the next hour, where one was posted. Later hours persist."""

    name = "rtd"
    look_ahead = False

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        issued = _issued(observation)
        last = _last_prices(observation)
        indicated = _rtd_mean(observation.get("forecasts", {}), issued)
        hours: dict[str, list[float]] = {series: [] for series in PRICE_SERIES}
        for step in range(horizon_hours):
            for series in PRICE_SERIES:
                if step == 0 and series in indicated:
                    hours[series].append(indicated[series])
                else:
                    hours[series].append(last[series])
        return _point(observation, horizon_hours, quantiles, hours)


class NetLoad:
    """price ~ net load (load − wind − solar) + outage capacity, fit on hours before the decision."""

    name = "net_load"
    look_ahead = False

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        issued = _issued(observation)
        last = _last_prices(observation)
        training = _training_rows(observation)
        levels = [float(q) for q in quantiles]
        by_series: dict[str, list[list[float]]] = {}
        for series in PRICE_SERIES:
            fit = _fit(training, series)
            columns = []
            for step in range(horizon_hours):
                feature = net_load_at(observation.get("forecasts", {}), issued + pd.Timedelta(hours=step))
                if fit is None or feature is None:
                    columns.append([last[series]] * len(levels))
                else:
                    net, outage = feature
                    columns.append([fit.at(net, outage, q) for q in levels])
            # columns[hour][quantile] -> values[quantile][hour]
            by_series[series] = [[hour[i] for hour in columns] for i in range(len(levels))]
        return _trajectory(observation, horizon_hours, levels, by_series)


class Oracle:
    """The prices that actually realized. It reads the market frame, not the observation.

    `look_ahead` is true and the name says so. It is not a fair forecaster.
    """

    name = "oracle (look-ahead)"
    look_ahead = True

    def __init__(self, intervals: pd.DataFrame):
        from harness.forecaster.grade import _intervals

        self._frame = _intervals(intervals)

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        issued = _issued(observation)
        hours: dict[str, list[float]] = {series: [] for series in PRICE_SERIES}
        for step in range(horizon_hours):
            start = issued + pd.Timedelta(hours=step)
            for series in PRICE_SERIES:
                value = realized_price(self._frame, start, pd.Timedelta(hours=1), series)
                hours[series].append(0.0 if value is None else value)
        return _point(observation, horizon_hours, quantiles, hours)


@dataclass(frozen=True)
class _Fit:
    intercept: float
    net: float
    outage: float
    sigma: float

    def at(self, net: float, outage: float, quantile: float) -> float:
        mid = self.intercept + self.net * net + self.outage * outage
        if self.sigma == 0.0 or math.isclose(quantile, 0.5):
            return mid
        return mid + _NORMAL.inv_cdf(quantile) * self.sigma


def _fit(rows: list[tuple[tuple[float, float], dict[str, float]]], series: str) -> _Fit | None:
    usable = [(net, outage, prices[series]) for (net, outage), prices in rows if series in prices]
    if len(usable) < 3:
        return None
    design = np.array([[1.0, net, outage] for net, outage, _price in usable], dtype=float)
    target = np.array([price for _net, _outage, price in usable], dtype=float)
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coef
    sigma = float(np.sqrt(np.mean(residual ** 2)))
    if sigma < 1e-8:
        sigma = 0.0
    return _Fit(float(coef[0]), float(coef[1]), float(coef[2]), sigma)


def _training_rows(observation: Mapping[str, Any]) -> list[tuple[tuple[float, float], dict[str, float]]]:
    features = {}
    for row in observation.get("history", {}).get("features", []):
        hour = pd.Timestamp(row["valid_time"]).tz_convert("UTC")
        features[hour] = (float(row["net_load_mw"]), float(row["outage_mw"]))
    buckets: dict[pd.Timestamp, dict[str, list[float]]] = {}
    for row in observation.get("history", {}).get("realized", []):
        hour = pd.Timestamp(row["valid_time"]).floor("h").tz_convert("UTC")
        if hour not in features:
            continue
        for series, value in row["prices"].items():
            if value is None:
                continue
            buckets.setdefault(hour, {}).setdefault(series, []).append(float(value))
    paired = []
    for hour, prices in buckets.items():
        paired.append((features[hour], {series: sum(values) / len(values) for series, values in prices.items()}))
    return paired


def _issued(observation: Mapping[str, Any]) -> pd.Timestamp:
    return pd.Timestamp(observation["now"]["interval_start_utc"]).tz_convert("UTC")


def _last_prices(observation: Mapping[str, Any]) -> dict[str, float]:
    chosen: dict[str, tuple[pd.Timestamp, float]] = {}
    for row in observation.get("history", {}).get("realized", []):
        stamp = pd.Timestamp(row["valid_time"])
        for series, value in row["prices"].items():
            if value is None or series not in PRICE_SERIES:
                continue
            previous = chosen.get(series)
            if previous is None or stamp >= previous[0]:
                chosen[series] = (stamp, float(value))
    return {series: (chosen[series][1] if series in chosen else 0.0) for series in PRICE_SERIES}


def _dam_prices(forecasts: Mapping[str, Any], hour: pd.Timestamp) -> dict[str, float]:
    found = {}
    for series in _ZONES:
        value = _one(forecasts.get("dam_spp", []), hour, (series,))
        if value is not None:
            found[series] = value
    for series, names in _MCPC.items():
        value = _one(forecasts.get("dam_mcpc", []), hour, names)
        if value is not None:
            found[series] = value
    return found


def _one(rows: list[dict[str, Any]], hour: pd.Timestamp, names: tuple[str, ...]) -> float | None:
    for row in rows:
        if str(row["series"]) not in names:
            continue
        valid = pd.Timestamp(row["valid_time"])
        if valid.tzinfo is None or valid.tz_convert("UTC") != hour:
            continue
        return float(row["value"])
    return None


def _rtd_mean(forecasts: Mapping[str, Any], issued: pd.Timestamp) -> dict[str, float]:
    end = issued + pd.Timedelta(hours=1)
    buckets: dict[str, list[float]] = {series: [] for series in PRICE_SERIES}
    pairs = [(zone, "rtd_lmp", zone) for zone in _ZONES]
    pairs.append(("MCPC_ECRS", "rtd_mcpc", "ECRS"))
    for name in NSPIN_NAMES:
        pairs.append(("MCPC_NSPIN", "rtd_mcpc", name))
    for target, source, series in pairs:
        for row in forecasts.get(source, []):
            if str(row["series"]) != series:
                continue
            valid = pd.Timestamp(row["valid_time"])
            if valid.tzinfo is None:
                continue
            valid = valid.tz_convert("UTC")
            if issued <= valid < end:
                buckets[target].append(float(row["value"]))
    return {series: sum(values) / len(values) for series, values in buckets.items() if values}


def _point(observation: Mapping[str, Any], horizon_hours: int, quantiles: Sequence[float],
           hours: Mapping[str, list[float]]) -> dict[str, Any]:
    levels = [float(q) for q in quantiles]
    values = {series: [[float(v) for v in hours[series]] for _ in levels] for series in PRICE_SERIES}
    return _trajectory(observation, horizon_hours, levels, values)


def _trajectory(observation: Mapping[str, Any], horizon_hours: int, quantiles: Sequence[float],
                values: Mapping[str, list[list[float]]]) -> dict[str, Any]:
    return {
        "issued_at": observation["now"]["interval_start_utc"],
        "horizon_hours": horizon_hours,
        "series": {
            name: {"quantiles": [float(q) for q in quantiles], "values": values[name]}
            for name in PRICE_SERIES
        },
    }
