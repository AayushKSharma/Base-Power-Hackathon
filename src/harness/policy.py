"""Capacity policies: each interval, decide the MW of each product to report to ERCOT."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from harness.base_actual import BASE_QSE, load_base_actual
from harness.observation import Observation
from harness.products import PRODUCTS
from harness.scenario import Scenario

# Reported capability in MW per product, e.g. {"ECRS": 40.0, "NONSPIN": 40.0}.
Capability = Mapping[str, float]


class Policy(Protocol):
    """Called once per 5-minute interval of a run, returning MW for every product.

    In quantile mode's default "typical" view there is one call per interval,
    and the answer is scored against every quantile. In the "per_case" view
    there is one call per interval for each quantile (five in all).

    Each operating day is simulated on its own, so a policy must not carry
    state from one day to the next, or a range run would no longer equal its
    single-day runs. In the "per_case" view it must not carry state from one
    quantile to the next either.
    """

    @property
    def name(self) -> str:
        """Label for the scorecard, including any parameters."""
        ...

    def decide(self, observation: Observation) -> Capability: ...


class PolicyError(ValueError):
    """An unknown built-in policy or an invalid policy parameter."""


@dataclass(frozen=True)
class ConstantHaircut:
    """Report a fixed fraction of the fleet's observed capability for every product."""

    fraction: float

    @property
    def name(self) -> str:
        return f"constant_haircut(fraction={self.fraction:g})"

    def decide(self, observation: Observation) -> Capability:
        # Multiply before dividing by 1000, matching examples/constant_haircut_policy.py,
        # so an external copy of this haircut reports the same bits.
        regions = observation["fleet"]["regions"]
        return {product: self.fraction * sum(r["capability_kw"][product] for r in regions) / 1000
                for product in PRODUCTS}


def _constant_haircut(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return ConstantHaircut(fraction=_float_param(params, "fraction", default=0.9))


@dataclass(frozen=True)
class BaseActual:
    """Replay Base's historical ADER AS awards as the reported capability K.

    One entry per 5-minute interval: ECRS MW, then Non-Spin MW, summed across
    Base's aggregate load resources. An interval with no award row reports 0.
    """

    awards: Mapping[pd.Timestamp, tuple[float, float]]

    @property
    def name(self) -> str:
        return "base_actual"

    def decide(self, observation: Observation) -> Capability:
        when = pd.Timestamp(observation["now"]["interval_start_utc"]).tz_convert("UTC")
        ecrs, nspin = self.awards.get(when, (0.0, 0.0))
        return {"ECRS": ecrs, "NONSPIN": nspin}


def base_actual_policy(start, end, *, store_dir=None) -> BaseActual:
    """Base's awards over [start, end]. Raises MarketDataMissing if a day is absent."""
    rows = load_base_actual(start, end, qse=BASE_QSE, store_dir=store_dir)
    last = rows.sort_values("sced_time_utc").groupby(["interval_start_utc", "resource"], sort=False).tail(1)
    summed = last.groupby("interval_start_utc")[["as_award_ecrs_mw", "as_award_nspin_mw"]].sum()
    awards = {
        pd.Timestamp(ts).tz_convert("UTC"): (float(ecrs), float(nspin))
        for ts, ecrs, nspin in summed.itertuples(index=True, name=None)
    }
    return BaseActual(awards)


def _base_actual(params: Mapping[str, str], scenario: Scenario) -> Policy:
    raise PolicyError("base_actual is built from the Base-actual dataset for the run's date range")


# Built-in policy name -> (accepted parameters, builder).
BUILTIN: dict[str, tuple[tuple[str, ...], Callable[[Mapping[str, str], Scenario], Policy]]] = {
    "constant_haircut": (("fraction",), _constant_haircut),
    "base_actual": ((), _base_actual),
}


def builtin_policy(name: str, params: Mapping[str, str], scenario: Scenario) -> Policy:
    """A built-in policy by name, with string parameters as given on the command line."""
    if name not in BUILTIN:
        raise PolicyError(f"unknown policy {name!r}; built-in policies: {', '.join(BUILTIN)}")
    accepted, build = BUILTIN[name]
    unknown = sorted(set(params) - set(accepted))
    if unknown:
        raise PolicyError(f"{name}: unknown parameter {unknown[0]!r}; accepted: {', '.join(accepted)}")
    return build(params, scenario)


def _float_param(params: Mapping[str, str], key: str, default: float) -> float:
    """params[key] as a finite non-negative float, or `default` when not given."""
    if key not in params:
        return default
    try:
        value = float(params[key])
    except ValueError:
        raise PolicyError(f"{key}: expected a number, got {params[key]!r}") from None
    if not math.isfinite(value) or value < 0:
        raise PolicyError(f"{key}: expected a non-negative number, got {params[key]!r}")
    return value
