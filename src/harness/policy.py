"""Capacity policies: each interval, decide the MW of each product to report to ERCOT."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from harness.observation import Observation
from harness.products import PRODUCTS
from harness.reference import Belief, CorrelatedNewsvendor, IndependentNewsvendor, ReliabilityTarget
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
    """Report a fixed fraction of the fleet's observed capability for every product.

    The belief is recorded on the scorecard. The fraction does not use it:
    a fixed haircut is the baseline that ignores prices and failure rates.
    """

    fraction: float
    belief: Belief | None = None

    @property
    def name(self) -> str:
        return f"constant_haircut(fraction={self.fraction:g})"

    @property
    def beliefs(self) -> dict[str, float]:
        return (self.belief if self.belief is not None else Belief()).as_dict()

    def decide(self, observation: Observation) -> Capability:
        # Multiply before dividing by 1000, matching examples/constant_haircut_policy.py,
        # so an external copy of this haircut reports the same bits.
        regions = observation["fleet"]["regions"]
        return {product: self.fraction * sum(r["capability_kw"][product] for r in regions) / 1000
                for product in PRODUCTS}


def _constant_haircut(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return ConstantHaircut(fraction=_float_param(params, "fraction", default=0.9), belief=_belief(params))


def _independent_newsvendor(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return IndependentNewsvendor(belief=_belief(params))


def _reliability_target(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return ReliabilityTarget(epsilon=_unit_param(params, "epsilon", default=0.05), belief=_belief(params))


def _correlated_newsvendor(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return CorrelatedNewsvendor(samples=_count_param(params, "samples", default=64), belief=_belief(params))


_BELIEF_KEYS = tuple(Belief().as_dict())


# Built-in policy name -> (accepted parameters, builder).
BUILTIN: dict[str, tuple[tuple[str, ...], Callable[[Mapping[str, str], Scenario], Policy]]] = {
    "constant_haircut": (("fraction", *_BELIEF_KEYS), _constant_haircut),
    "independent_newsvendor": (_BELIEF_KEYS, _independent_newsvendor),
    "reliability_target": (("epsilon", *_BELIEF_KEYS), _reliability_target),
    "correlated_newsvendor": (("samples", *_BELIEF_KEYS), _correlated_newsvendor),
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


def _belief(params: Mapping[str, str]) -> Belief:
    """Belief fields present in `params`. The rest stay at baseline calm conditions."""
    return Belief(**{key: _float_param(params, key, default=0.0) for key in _BELIEF_KEYS if key in params})


def _count_param(params: Mapping[str, str], key: str, default: int) -> int:
    """params[key] as an integer of at least 1, or `default` when not given."""
    if key not in params:
        return default
    try:
        value = int(params[key])
    except ValueError:
        raise PolicyError(f"{key}: expected an integer, got {params[key]!r}") from None
    if value < 1:
        raise PolicyError(f"{key}: expected an integer of at least 1, got {params[key]!r}")
    return value


def _unit_param(params: Mapping[str, str], key: str, default: float) -> float:
    """params[key] as a probability in [0, 1], or `default` when not given."""
    value = _float_param(params, key, default=default)
    if value > 1:
        raise PolicyError(f"{key}: expected a number from 0 to 1, got {params.get(key, value)!r}")
    return value


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
