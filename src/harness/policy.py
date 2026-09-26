"""Capacity policies: each interval, decide the MW of each product to report to ERCOT."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from harness.observation import Observation, observed_capability_mw
from harness.products import PRODUCTS
from harness.scenario import Scenario

# Reported capability in MW per product, e.g. {"ECRS": 40.0, "NONSPIN": 40.0}.
Capability = Mapping[str, float]


class Policy(Protocol):
    """Called once per 5-minute interval of a run, for each fleet case.

    Each operating day and each fleet case (quantile mock) is simulated on its
    own, so a policy must not carry state from one day or case to the next;
    otherwise a range run would no longer equal its single-day runs, and one
    quantile's decisions would leak into another's.
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
        return {product: self.fraction * observed_capability_mw(observation, product)
                for product in PRODUCTS}


def _constant_haircut(params: Mapping[str, str], scenario: Scenario) -> Policy:
    return ConstantHaircut(fraction=_float_param(params, "fraction", default=0.9))


# Built-in policy name -> (accepted parameters, builder).
BUILTIN: dict[str, tuple[tuple[str, ...], Callable[[Mapping[str, str], Scenario], Policy]]] = {
    "constant_haircut": (("fraction",), _constant_haircut),
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
