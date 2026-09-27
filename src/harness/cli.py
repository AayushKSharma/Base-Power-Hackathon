"""harness: score capacity policies on real ERCOT market data.

    harness run --scenario scenarios/baseline.yaml --start 2026-03-08 --end 2026-03-09
    harness compare --policy constant_haircut --policy base_actual --scenario baseline --start 2026-03-08 --out compare
    harness replay --scenario scenarios/baseline.yaml --day 2026-03-08 --minutes 5 --out replay
    harness value --policy independent_newsvendor --forecaster persistence --scenario baseline --start 2026-03-08 --out value

Prints a scorecard per fleet case (quantile mock P10..P90, or stochastic) and
writes scorecard.json, a markdown report (report.md) and the per-interval
data dump (intervals.parquet) to the output directory.

The run farm evaluates a sweep in parallel:

    harness submit --sweep sweep.yaml
    harness work --market-dir data/market
    harness aggregate --out data/runs/sweep
    harness status
    harness chaos --sweep sweep.yaml --seed 11 --workers 4
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

import psycopg

from harness.base_actual import BASE_QSE, grade_delivery, load_base_actual
from harness.compare import write_comparison
from harness.external import ExternalPolicy
from harness.farm import DEFAULT_DSN, Farm, load_sweep
from harness.forecaster.cli import add_parser as add_forecast_parser
from harness.forecaster.cli import build_forecaster, run as forecast_command
from harness.forecast import ForecastStore
from harness.forecast.catalog import DEFAULT_HORIZON
from harness.market import MarketDataMissing, load_intervals
from harness.market.dataset import default_store_dir
from harness.paths import runs_dir
from harness.policy import BUILTIN, Policy, PolicyError, base_actual_policy, builtin_policy
from harness.replay import ReplayError, replay as replay_day
from harness.report import render_report
from harness.runner import INTERVALS_FILE, REPORT_FILE, SCORECARD_FILE, run
from harness.value import measure_forecast_value, render_forecast_value, write_forecast_value
from harness.scenario import Scenario, ScenarioError, load_scenario, resolve_scenario
from harness.scorecard import render, to_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="score one policy on one scenario over a date range")
    run_cmd.add_argument("--policy", default="constant_haircut",
                         help=f"built-in policy ({', '.join(BUILTIN)}) or an external command "
                              "(default constant_haircut)")
    run_cmd.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="built-in policy parameter, repeatable. constant_haircut takes fraction "
                              "(default 0.9); reliability_target takes epsilon (default 0.05); "
                              "correlated_newsvendor takes samples (default 64). Every reference "
                              "policy also takes belief parameters such as compliance_per_mw")
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
    run_cmd.add_argument("--forecaster",
                         help="price forecaster whose output fills each observation "
                              "(persistence, dam, rtd, net_load, oracle, or an external command)")
    run_cmd.add_argument("--out", type=Path, help=f"output directory (default {runs_dir()}/<run name>)")

    add_forecast_parser(sub)
    _add_farm_parsers(sub)

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
    compare.add_argument("--forecaster",
                         help="price forecaster whose output fills each observation; the comparison "
                              "report then includes its value against the oracle and persistence")
    compare.add_argument("--out", type=Path, required=True, help="output directory")

    replay_cmd = sub.add_parser("replay", help="play one operating day on a 2-second simulated clock")
    replay_cmd.add_argument("--policy", default="constant_haircut",
                            help=f"built-in policy ({', '.join(BUILTIN)}) or an external command "
                                 "(default constant_haircut)")
    replay_cmd.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                            help="built-in policy parameter, repeatable")
    replay_cmd.add_argument("--decision-timeout", type=float, default=1.0,
                            help="seconds an external policy has to answer one decision (default 1)")
    replay_cmd.add_argument("--fallback", choices=("last_good", "zero"), default="last_good",
                            help="capability used when an external policy times out, crashes, or replies "
                                 "badly (default last_good)")
    replay_cmd.add_argument("--scenario", required=True,
                            help="scenario YAML file, or a preset name")
    replay_cmd.add_argument("--day", required=True, type=dt.date.fromisoformat,
                            help="operating day (CPT), YYYY-MM-DD")
    replay_cmd.add_argument("--minutes", type=float,
                            help="simulated minutes from the start of the day (default: the whole day)")
    replay_cmd.add_argument("--tick", type=float, default=2.0,
                            help="simulated seconds per tick (default 2)")
    replay_cmd.add_argument("--speed", type=float,
                            help="simulated seconds per wall-clock second (default: as fast as possible)")
    replay_cmd.add_argument("--seed", type=_seed, help="random seed (default: the scenario's seed)")
    replay_cmd.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    replay_cmd.add_argument("--out", type=Path, required=True, help="directory for timeline.json")

    value = sub.add_parser(
        "value",
        help="score one policy with a forecaster, the oracle, and persistence",
    )
    value.add_argument("--policy", required=True,
                       help=f"built-in policy ({', '.join(BUILTIN)}) or an external command")
    value.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                       help="built-in policy parameter, repeatable")
    value.add_argument("--forecaster", required=True,
                       help="price forecaster to value against the oracle and persistence")
    value.add_argument("--scenario", required=True, help="scenario YAML file, or a preset name")
    value.add_argument("--start", required=True, type=dt.date.fromisoformat,
                       help="first operating day (CPT), YYYY-MM-DD")
    value.add_argument("--end", type=dt.date.fromisoformat, help="last operating day (default: --start)")
    value.add_argument("--seed", type=_seed, help="random seed (default: the scenario's seed)")
    value.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    value.add_argument("--decision-timeout", type=float, default=1.0,
                       help="seconds an external policy or forecaster has to answer (default 1)")
    value.add_argument("--fallback", choices=("last_good", "zero"), default="last_good",
                       help="used when an external policy or forecaster fails (default last_good)")
    value.add_argument("--out", type=Path, required=True, help="output directory")

    args = parser.parse_args(argv)
    if args.command == "forecast":
        return forecast_command(args)
    if args.command in {"submit", "work", "aggregate", "status", "chaos"}:
        return _farm_command(args)
    if args.command == "compare":
        try:
            return _compare(args)
        except (ScenarioError, PolicyError) as e:
            print(f"harness: error: {e}", file=sys.stderr)
            return 2
        except MarketDataMissing as e:
            print(f"harness: error: {e}", file=sys.stderr)
            return 1
    if args.command == "replay":
        try:
            return _replay(args)
        except (ScenarioError, PolicyError, ReplayError) as e:
            print(f"harness: error: {e}", file=sys.stderr)
            return 2
        except MarketDataMissing as e:
            print(f"harness: error: {e}", file=sys.stderr)
            return 1
    if args.command == "value":
        try:
            return _value(args)
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
    frame = market(args.start, end) if args.forecaster else None
    results = []
    sections = []
    for spec in args.policy:
        def factory(spec: str = spec) -> Policy:
            return _policy(spec, {}, scenario, 1.0, "last_good",
                           start=args.start, end=end, store_dir=args.market_dir)

        if args.forecaster and frame is not None:
            report, result = measure_forecast_value(
                factory, scenario, args.start, end, args.seed, market, args.forecaster, frame)
            results.append(result)
            sections.append(render_forecast_value(report))
        else:
            results.append(run(factory(), scenario, args.start, end, args.seed, market=market))
    write_comparison(results, scenario, args.out,
                     forecast_value="\n".join(sections) if sections else None)
    print(f"Wrote {args.out / 'frontier.svg'} and exceedance.md")
    return 0


def _replay(args: argparse.Namespace) -> int:
    scenario = load_scenario(resolve_scenario(args.scenario))
    policy = _policy(args.policy, _params(args.param), scenario, args.decision_timeout, args.fallback,
                     start=args.day, end=args.day, store_dir=args.market_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    timeline = args.out / "timeline.json"
    result = replay_day(
        policy, scenario, args.day, seed=args.seed, minutes=args.minutes, tick_s=args.tick,
        speed=args.speed, market=functools.partial(load_intervals, store_dir=args.market_dir),
        out=timeline,
    )
    print(f"Wrote {timeline} ({len(result.ticks)} ticks)")
    return 0


def _value(args: argparse.Namespace) -> int:
    end = args.end or args.start
    if end < args.start:
        print(f"harness: error: --end {end} is before --start {args.start}", file=sys.stderr)
        return 2
    scenario = load_scenario(resolve_scenario(args.scenario))
    market = functools.partial(load_intervals, store_dir=args.market_dir)
    frame = market(args.start, end)
    params = _params(args.param)

    def factory() -> Policy:
        return _policy(args.policy, params, scenario, args.decision_timeout, args.fallback,
                       start=args.start, end=end, store_dir=args.market_dir)

    report, _chosen = measure_forecast_value(
        factory, scenario, args.start, end, args.seed, market, args.forecaster, frame,
        timeout_s=args.decision_timeout, fallback=args.fallback)
    write_forecast_value(report, args.out)
    sys.stdout.write(render_forecast_value(report))
    print(f"Wrote {args.out / 'value.json'} and {args.out / 'value.svg'}")
    return 0


def _run(args: argparse.Namespace, end: dt.date) -> int:
    scenario = load_scenario(resolve_scenario(args.scenario))
    policy = _policy(args.policy, _params(args.param), scenario, args.decision_timeout, args.fallback,
                     start=args.start, end=end, store_dir=args.market_dir)
    forecasts = ForecastStore(args.forecasts) if args.forecasts is not None else None
    market = functools.partial(load_intervals, store_dir=args.market_dir)
    forecaster = None
    if args.forecaster:
        forecaster = build_forecaster(
            args.forecaster, market(args.start, end), args.decision_timeout, args.fallback)
    result = run(policy, scenario, args.start, end, args.seed,
                 market=market,
                 forecasts=forecasts,
                 forecast_horizon=dt.timedelta(hours=args.forecast_horizon),
                 forecaster=forecaster)
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


def _add_farm_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    submit = sub.add_parser("submit", help="expand a sweep into one job per policy, scenario, day and seed")
    submit.add_argument("--sweep", required=True, type=Path, help="sweep YAML file")
    submit.add_argument("--database", default=DEFAULT_DSN, help=f"Postgres URL (default {DEFAULT_DSN})")

    work = sub.add_parser("work", help="claim jobs and run the harness until the queue is empty")
    work.add_argument("--database", default=DEFAULT_DSN, help=f"Postgres URL (default {DEFAULT_DSN})")
    work.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    work.add_argument("--worker-id", help="lease owner (default hostname and pid)")
    work.add_argument("--lease-seconds", type=float, default=30,
                      help="how long a claim lasts before another worker may take it (default 30)")
    work.add_argument("--max-attempts", type=int, default=3,
                      help="times a raising job is tried before it is failed (default 3)")
    work.add_argument("--backoff-seconds", type=float, default=1,
                      help="base delay before a failed attempt is tried again; doubles each time (default 1)")

    aggregate = sub.add_parser("aggregate", help="build scorecards from per-day results")
    aggregate.add_argument("--database", default=DEFAULT_DSN, help=f"Postgres URL (default {DEFAULT_DSN})")
    aggregate.add_argument("--out", type=Path, help="directory for one scorecard JSON per policy, scenario and seed")

    status = sub.add_parser("status", help="queued, leased, done and failed counts, plus throughput")
    status.add_argument("--database", default=DEFAULT_DSN, help=f"Postgres URL (default {DEFAULT_DSN})")

    chaos = sub.add_parser("chaos", help="run a sweep while killing workers at seeded times")
    chaos.add_argument("--sweep", required=True, type=Path, help="sweep YAML file")
    chaos.add_argument("--seed", required=True, type=_seed, help="seed for which workers die, and when")
    chaos.add_argument("--workers", type=int, default=2, help="workers to run at once (default 2)")
    chaos.add_argument("--database", default=DEFAULT_DSN, help=f"Postgres URL (default {DEFAULT_DSN})")
    chaos.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    chaos.add_argument("--lease-seconds", type=float, default=30,
                       help="how long a claim lasts before another worker may take it (default 30)")
    chaos.add_argument("--max-attempts", type=int, default=3,
                       help="times a raising job is tried before it is failed (default 3)")
    chaos.add_argument("--backoff-seconds", type=float, default=1,
                       help="base delay before a failed attempt is tried again (default 1)")


def _farm_command(args: argparse.Namespace) -> int:
    try:
        if args.command == "submit":
            farm = Farm(args.database, market_dir=None)
            inserted = farm.submit(load_sweep(args.sweep))
            print(f"submitted {inserted} jobs")
            return 0
        if args.command == "work":
            if args.lease_seconds <= 0:
                print("harness: error: --lease-seconds must be positive", file=sys.stderr)
                return 2
            if args.max_attempts < 1:
                print("harness: error: --max-attempts must be at least 1", file=sys.stderr)
                return 2
            if args.backoff_seconds < 0:
                print("harness: error: --backoff-seconds must not be negative", file=sys.stderr)
                return 2
            worker = args.worker_id or f"{os.uname().nodename}-{os.getpid()}"
            farm = Farm(args.database, market_dir=args.market_dir,
                        lease=dt.timedelta(seconds=args.lease_seconds),
                        max_attempts=args.max_attempts,
                        backoff=dt.timedelta(seconds=args.backoff_seconds))
            done = 0
            while True:
                done += farm.work(worker)
                report = farm.status()
                if report.queued == 0 and report.leased == 0:
                    break
                time.sleep(0.05)
            print(f"{worker} completed {done} jobs")
            return 0
        if args.command == "chaos":
            if args.lease_seconds <= 0 or args.workers < 1:
                print("harness: error: --lease-seconds and --workers must be positive", file=sys.stderr)
                return 2
            if args.market_dir is None:
                args.market_dir = default_store_dir()
            farm = Farm(args.database, market_dir=args.market_dir,
                        lease=dt.timedelta(seconds=args.lease_seconds),
                        max_attempts=args.max_attempts,
                        backoff=dt.timedelta(seconds=args.backoff_seconds))
            inserted = farm.submit(load_sweep(args.sweep))
            chaos_report = farm.chaos(workers=args.workers, seed=args.seed, kill="both")
            print(f"submitted {inserted} jobs")
            print(
                f"killed {chaos_report.killed_mid_job} workers mid-job, "
                f"{chaos_report.killed_before_commit} before commit"
            )
            print(f"completed {chaos_report.committed} jobs")
            return 0
        if args.command == "aggregate":
            farm = Farm(args.database, market_dir=None)
            cards = farm.aggregate()
            if args.out is not None:
                args.out.mkdir(parents=True, exist_ok=True)
                for (policy, scenario, seed), scorecards in sorted(cards.items()):
                    name = _slug(f"{scenario}_{policy}_seed{seed}")
                    (args.out / f"{name}.json").write_text(to_json(list(scorecards.values())))
            print(f"aggregated {len(cards)} scorecards")
            return 0
        report = Farm(args.database, market_dir=None).status()
        print(f"queued {report.queued}  leased {report.leased}  done {report.done}  failed {report.failed}")
        print(f"throughput {report.throughput_per_s:.3f} jobs/s")
        for error in report.errors:
            print(f"failed: {error}")
        return 0
    except (OSError, ValueError, PolicyError, psycopg.Error) as exc:
        print(f"harness: error: {exc}", file=sys.stderr)
        return 2
