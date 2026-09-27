#!/usr/bin/env python3
"""Reference policies for the harness JSON-lines protocol.

Speaks the same loop as examples/constant_haircut_policy.py. Belief parameters
default to baseline calm conditions and are reported on the hello, so the
scorecard matches the in-process policy.

    python examples/reference_policy.py --policy independent_newsvendor
    python examples/reference_policy.py --policy correlated_newsvendor --samples 64
    python examples/reference_policy.py --policy reliability_target --epsilon 0.05
    python examples/reference_policy.py --policy constant_haircut --fraction 0.9
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any

from harness.policy import ConstantHaircut
from harness.reference import Belief, CorrelatedNewsvendor, IndependentNewsvendor, ReliabilityTarget
from harness.rng import RandomStreams


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", required=True, choices=(
        "constant_haircut", "independent_newsvendor", "correlated_newsvendor", "reliability_target",
    ))
    parser.add_argument("--fraction", type=float, default=0.9)
    parser.add_argument("--epsilon", type=float, default=0.05)
    parser.add_argument("--samples", type=int, default=64)
    for key in Belief().as_dict():
        parser.add_argument(f"--{key}", type=float, default=None)
    args = parser.parse_args()
    if args.fraction < 0 or not 0 <= args.epsilon <= 1 or args.samples < 1:
        parser.error("fraction and samples must be positive, epsilon must be from 0 to 1")

    belief = Belief(**{key: getattr(args, key) for key in Belief().as_dict() if getattr(args, key) is not None})
    if args.policy == "constant_haircut":
        policy: Any = ConstantHaircut(fraction=args.fraction, belief=belief)
    elif args.policy == "independent_newsvendor":
        policy = IndependentNewsvendor(belief=belief)
    elif args.policy == "correlated_newsvendor":
        policy = CorrelatedNewsvendor(samples=args.samples, belief=belief)
    else:
        policy = ReliabilityTarget(epsilon=args.epsilon, belief=belief)

    hello = _read()
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        print("expected a hello message", file=sys.stderr)
        return 2
    _send({"name": policy.name, "version": "1", "wants_per_home": False, "beliefs": policy.beliefs})

    armed: tuple[Any, ...] | None = None
    while True:
        message = _read()
        if message is None:
            return 0
        if not isinstance(message, dict) or message.get("type") != "observation":
            print("expected an observation message", file=sys.stderr)
            return 2
        token = _random_token(message.get("random"))
        if token != armed and token is not None and hasattr(policy, "arm"):
            policy.arm(RandomStreams(str(token[0]), int(token[1])), dt.date.fromisoformat(str(token[2])))
            armed = token
        _send({"capability_mw": policy.decide(message["observation"])})


def _random_token(random: Any) -> tuple[Any, ...] | None:
    if not isinstance(random, dict):
        return None
    scenario, seed, day = random.get("scenario"), random.get("seed"), random.get("day")
    if scenario is None or seed is None or day is None:
        return None
    return (scenario, seed, day)


def _read() -> Any:
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
