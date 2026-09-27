"""harness: score capacity policies on real ERCOT market data.

    harness run --scenario scenarios/baseline.yaml --start 2026-03-08 --end 2026-03-09
    harness compare --policy constant_haircut --policy base_actual --scenario baseline --start 2026-03-08 --out compare

Prints a scorecard per fleet case (quantile mock P10..P90, or stochastic) and
writes scorecard.json, a markdown report (report.md) and the per-interval
data dump (intervals.parquet) to the output directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import re
import shlex
import sys
from pathlib import Path

from harness.base_actual import BASE_QSE, grade_delivery, load_base_actual
from harness.compare import write_comparison
from harness.external import ExternalPolicy
from harness.forecast import ForecastStore
from harness.forecast.catalog import DEFAULT_HORIZON
from harness.market import MarketDataMissing, load_intervals
from harness.market.dataset import default_store_dir
from harness.paths import runs_dir
from harness.policy import BUILTIN, Policy, PolicyError, base_actual_policy, builtin_policy
from harness.report import render_report
from harness.runner import INTERVALS_FILE, REPORT_FILE, SCORECARD_FILE, run
from harness.scenario import Scenario, ScenarioError, load_scenario, resolve_scenario
from harness.scorecard import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="score one policy on one scenario over a date range")
    run_cmd.add_argument("--policy", default="constant_haircut",
                         help=f"built-in policy ({', '.join(BUILTIN)}) or an external command "
                              "(default constant_haircut)")
    run_cmd.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="built-in policy parameter, repeatable; constant_haircut takes fraction (default 0.9)")
    run_cmd.add_argument("--decision-timeout", type=float, default=1.0,
                         help="seconds an external policy has to answer one decision (default 1)")
    run_cmd.add_argument("--fallback", choices=("last_good", "zero"), default="last_good",
                         help="capability used when an external policy times out, crashes, or replies "
                              "badly (default last_good)")
    run_cmd.add_argument("--scenario", required=True,
                         help="scenario YAML file, or a preset name (baseline, storm_houston, "
                              "caps_lifted, nonspin_2h, ecrs_2h, fleet_10x)")
    run_cmd.add_argument("--start", required=True, type=dt.date.fromisoformat,
                         help="first operating day (CPT), YYYY-MM-DD")
    run_cmd.add_argument("--end", type=dt.date.fromisoformat, help="last operating day (default: --start)")
    run_cmd.add_argument("--seed", type=_seed, help="random seed (default: the scenario's seed)")
    run_cmd.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    run_cmd.add_argument("--forecasts", type=Path,
                         help="forecast-input store; fills each observation's forecasts from as_of")
    run_cmd.add_argument("--forecast-horizon", type=float, default=DEFAULT_HORIZON / dt.timedelta(hours=1),
                         help="hours of forecast valid time after each decision (default 168)")
    run_cmd.add_argument("--out", type=Path, help=f"output directory (default {runs_dir()}/<run name>)")

    from harness.forecaster.cli import add_parser as add_forecast_parser
    from harness.forecaster.cli import run as forecast_command

    add_forecast_parser(sub)

    compare = sub.add_parser("compare", help="score named policies and chart the revenue-vs-shortfall frontier")
    compare.add_argument("--policy", action="append", required=True,
                         help=f"built-in policy ({', '.join(BUILTIN)}), repeatable")
    compare.add_argument("--scenario", required=True,
                         help="scenario YAML file, or a preset name")
    compare.add_argument("--start", required=True, type=dt.date.fromisoformat,
                         help="first operating day (CPT), YYYY-MM-DD")
    compare.add_argument("--end", type=dt.date.fromisoformat, help="last operating day (default: --start)")
    compare.add_argument("--seed", type=_seed, help="random seed (default: the scenario's seed)")
    compare.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    compare.add_argument("--out", type=Path, required=True, help="output directory")

    args = parser.parse_args(argv)
    if args.command == "forecast":
        return forecast_command(args)
    try:
        if args.command == "compare":
            return _compare(args)
    except (ScenarioError, PolicyError) as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    except MarketDataMissing as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 1
    end = args.end or args.start
    if end < args.start:
        parser.error(f"--end {end} is before --start {args.start}")
    if args.forecast_horizon < 0:
        parser.error("--forecast-horizon must be non-negative")
    if args.forecasts is not None and not args.forecasts.is_dir():
        print(f"harness: error: forecast store {args.forecasts} is not a directory", file=sys.stderr)
        return 2
    try:
        return _run(args, end)
    except (ScenarioError, PolicyError) as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    except MarketDataMissing as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 1


def _compare(args: argparse.Namespace) -> int:
    end = args.end or args.start
    if end < args.start:
        print(f"harness: error: --end {end} is before --start {args.start}", file=sys.stderr)
        return 2
    scenario = load_scenario(resolve_scenario(args.scenario))
    market = functools.partial(load_intervals, store_dir=args.market_dir)
    results = []
    for spec in args.policy:
        policy = _policy(spec, {}, scenario, 1.0, "last_good",
                         start=args.start, end=end, store_dir=args.market_dir)
        results.append(run(policy, scenario, args.start, end, args.seed, market=market))
    write_comparison(results, scenario, args.out)
    print(f"Wrote {args.out / 'frontier.svg'} and exceedance.md")
    return 0


def _run(args: argparse.Namespace, end: dt.date) -> int:
    scenario = load_scenario(resolve_scenario(args.scenario))
    policy = _policy(args.policy, _params(args.param), scenario, args.decision_timeout, args.fallback,
                     start=args.start, end=end, store_dir=args.market_dir)
    forecasts = ForecastStore(args.forecasts) if args.forecasts is not None else None
    result = run(policy, scenario, args.start, end, args.seed,
                 market=functools.partial(load_intervals, store_dir=args.market_dir),
                 forecasts=forecasts,
                 forecast_horizon=dt.timedelta(hours=args.forecast_horizon))
    cards = list(result.scorecards.values())
    card = cards[0]
    out = args.out or runs_dir() / f"{card.scenario}_{_slug(card.policy)}_{card.start}_{card.end}_seed{card.seed}"
    result.write(out)
    report = render_report(result, scenario)
    wrote = [SCORECARD_FILE, INTERVALS_FILE, REPORT_FILE]
    if policy.name == "base_actual":
        rows = load_base_actual(args.start, end, qse=BASE_QSE, store_dir=args.market_dir)
        delivery = grade_delivery(rows)
        (out / "delivery.json").write_text(json.dumps(delivery, indent=2) + "\n")
        report = report.rstrip() + "\n\n" + _delivery_section(delivery)
        wrote.append("delivery.json")
    (out / REPORT_FILE).write_text(report)
    sys.stdout.write(render(cards))
    print(f"\nWrote {', '.join(str(out / name) for name in wrote)}")
    return 0


def _delivery_section(delivery: dict) -> str:
    """The real-world counterpart to the simulated shortfall metrics."""
    summary = delivery["summary"]
    lines = [
        "## Real delivery",
        "",
        "On Base's dispatch-down events, requested MW is the mean Base Point and "
        "delivered MW is the mean Real Power Consumption. The shortfall is the worst "
        "run's consumption above that Base Point. An event is within tolerance when "
        "that shortfall is within the lesser of 3% of requested MW and 3 MW.",
        "",
        "| Resource | Requested MW | Delivered MW | Shortfall MW | Within tolerance |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for event in delivery["events"]:
        lines.append(
            f"| {event['resource']} | {event['requested_mw']:.3f} | {event['delivered_mw']:.3f} | "
            f"{event['shortfall_mw']:.3f} | {'yes' if event['within_tolerance'] else 'no'} |"
        )
    share = summary["share_within_tolerance"]
    lines += [
        "",
        f"{summary['within_tolerance']} of {summary['events']} events within tolerance "
        f"({share:.0%}). Worst shortfall {summary['worst_shortfall_mw']:.3f} MW.",
        "",
    ]
    return "\n".join(lines)


def _policy(spec: str, params: dict[str, str], scenario: Scenario, timeout_s: float,
            fallback: str, *, start: dt.date, end: dt.date, store_dir: Path | None) -> Policy:
    """A built-in policy, or an external command parsed with shell quoting."""
    if spec == "base_actual":
        if params:
            raise PolicyError("base_actual takes no parameters")
        return base_actual_policy(start, end, store_dir=store_dir)
    if spec in BUILTIN:
        return builtin_policy(spec, params, scenario)
    if params:
        raise PolicyError("--param applies to built-in policies; put arguments in the --policy command")
    return ExternalPolicy(shlex.split(spec), scenario.products, timeout_s=timeout_s, fallback=fallback)


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
