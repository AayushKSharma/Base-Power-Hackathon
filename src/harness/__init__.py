"""Capacity-policy test harness: replay a policy on real ERCOT data and score it.

    from harness import ConstantHaircut, load_scenario, run
    result = run(ConstantHaircut(0.9), load_scenario("scenarios/baseline.yaml"), start, end, seed=7)
    result.scorecards["P10"].totals["ECRS"].oversold_mw_h
"""

from harness.external import ExternalPolicy
from harness.policy import ConstantHaircut, Policy, PolicyError
from harness.reference import (
    Belief,
    CorrelatedNewsvendor,
    IndependentNewsvendor,
    ReliabilityTarget,
)
from harness.rng import RandomStreams
from harness.runner import RunResult, run
from harness.scenario import Scenario, ScenarioError, load_scenario, parse_scenario
from harness.scorecard import Scorecard

__all__ = [
    "Belief",
    "ConstantHaircut",
    "CorrelatedNewsvendor",
    "ExternalPolicy",
    "IndependentNewsvendor",
    "Policy",
    "PolicyError",
    "ReliabilityTarget",
    "RandomStreams",
    "RunResult",
    "Scenario",
    "ScenarioError",
    "Scorecard",
    "load_scenario",
    "parse_scenario",
    "run",
]
