"""harness: score capacity policies on real ERCOT market data.

    harness run --scenario scenarios/minimal.yaml --start 2026-03-08 --end 2026-03-09

Prints the scorecard and writes scorecard.json plus the per-interval data dump
(intervals.parquet) to the output directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import re
import sys
from pathlib import Path

from harness.market import MarketDataMissing, load_intervals
from harness.market.dataset import default_store_dir
from harness.paths import runs_dir
from harness.policy import BUILTIN, PolicyError, builtin_policy
from harness.runner import INTERVALS_FILE, SCORECARD_FILE, run
from harness.scenario import ScenarioError, load_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="score one policy on one scenario over a date range")
    run_cmd.add_argument("--policy", default="constant_haircut",
                         help=f"built-in policy: {', '.join(BUILTIN)} (default constant_haircut)")
    run_cmd.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="policy parameter, repeatable; constant_haircut takes fraction (default 0.9)")
    run_cmd.add_argument("--scenario", required=True, type=Path, help="scenario YAML file")
    run_cmd.add_argument("--start", required=True, type=dt.date.fromisoformat,
                         help="first operating day (CPT), YYYY-MM-DD")
    run_cmd.add_argument("--end", type=dt.date.fromisoformat, help="last operating day (default: --start)")
    run_cmd.add_argument("--seed", type=_seed, help="random seed (default: the scenario's seed)")
    run_cmd.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    run_cmd.add_argument("--out", type=Path, help=f"output directory (default {runs_dir()}/<run name>)")

    args = parser.parse_args(argv)
    end = args.end or args.start
    if end < args.start:
        parser.error(f"--end {end} is before --start {args.start}")
    try:
        return _run(args, end)
    except (ScenarioError, PolicyError) as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    except MarketDataMissing as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace, end: dt.date) -> int:
    scenario = load_scenario(args.scenario)
    policy = builtin_policy(args.policy, _params(args.param), scenario)
    result = run(policy, scenario, args.start, end, args.seed,
                 market=functools.partial(load_intervals, store_dir=args.market_dir))
    card = result.scorecard
    out = args.out or runs_dir() / f"{card.scenario}_{_slug(card.policy)}_{card.start}_{card.end}_seed{card.seed}"
    result.write(out)
    sys.stdout.write(card.to_table())
    print(f"\nWrote {out / SCORECARD_FILE} and {INTERVALS_FILE}")
    return 0


def _params(pairs: list[str]) -> dict[str, str]:
    params = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise PolicyError(f"--param expects KEY=VALUE, got {pair!r}")
        params[key] = value
    return params


def _seed(text: str) -> int:
    seed = int(text)
    if seed < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer, got {text}")
    return seed


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._]+", "-", text).strip("-")
