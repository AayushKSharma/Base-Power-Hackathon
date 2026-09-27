"""harness forecast: grade price forecasters and write a leaderboard."""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import sys
from pathlib import Path

from harness.forecast import ForecastStore
from harness.forecast.catalog import DEFAULT_HORIZON
from harness.forecaster.external import ExternalForecaster
from harness.forecaster.grade import grade
from harness.forecaster.reference import dam_as_forecast, net_load, oracle, persistence, rtd_indicative
from harness.forecaster.report import publish
from harness.market import MarketDataMissing, load_intervals
from harness.market.dataset import default_store_dir
from harness.paths import runs_dir
from harness.policy import PolicyError

BUILTINS = {
    "persistence": persistence,
    "dam": dam_as_forecast,
    "rtd": rtd_indicative,
    "net_load": net_load,
}


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    forecast = sub.add_parser("forecast", help="grade price forecasters over a date range")
    add_arguments(forecast)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--forecaster", action="append", required=True, dest="forecasters",
                        metavar="NAME_OR_COMMAND",
                        help="built-in forecaster "
                             f"({', '.join([*BUILTINS, 'oracle'])}) or an external command; repeatable")
    parser.add_argument("--start", required=True, type=dt.date.fromisoformat,
                        help="first operating day (CPT), YYYY-MM-DD")
    parser.add_argument("--end", type=dt.date.fromisoformat, help="last operating day (default: --start)")
    parser.add_argument("--market-dir", type=Path, help=f"market dataset (default {default_store_dir()})")
    parser.add_argument("--forecasts", type=Path, help="forecast-input store, read only through as_of")
    parser.add_argument("--horizon", type=int, default=24, help="hours of prices to forecast (default 24)")
    parser.add_argument("--quantiles", default="0.1,0.5,0.9",
                        help="comma-separated quantiles; must include 0.5 (default 0.1,0.5,0.9)")
    parser.add_argument("--spike-top", type=float, default=0.1,
                        help="top fraction of realized prices counted as spikes (default 0.1)")
    parser.add_argument("--forecast-horizon", type=float, default=DEFAULT_HORIZON / dt.timedelta(hours=1),
                        help="hours of forecast-input valid time after each decision (default 168)")
    parser.add_argument("--decision-timeout", type=float, default=1.0,
                        help="seconds an external forecaster has to answer (default 1)")
    parser.add_argument("--fallback", choices=("last_good", "zero"), default="last_good",
                        help="forecast used when an external forecaster times out, crashes, or replies "
                             "badly (default last_good)")
    parser.add_argument("--out", type=Path, help="output directory (default runs/forecast-<start>_<end>)")


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m harness.forecaster`."""
    parser = argparse.ArgumentParser(prog="harness forecast", description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(args)


def run(args: argparse.Namespace) -> int:
    end = args.end or args.start
    if end < args.start:
        print(f"harness: error: --end {end} is before --start {args.start}", file=sys.stderr)
        return 2
    if args.horizon < 1:
        print("harness: error: --horizon must be at least 1", file=sys.stderr)
        return 2
    if args.forecasts is not None and not args.forecasts.is_dir():
        print(f"harness: error: forecast store {args.forecasts} is not a directory", file=sys.stderr)
        return 2
    try:
        quantiles = _quantiles(args.quantiles)
    except PolicyError as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    try:
        frame = load_intervals(args.start, end, store_dir=args.market_dir)
    except MarketDataMissing as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 1
    try:
        forecasters = [_build(spec, frame, args.decision_timeout, args.fallback) for spec in args.forecasters]
    except PolicyError as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    store = ForecastStore(args.forecasts) if args.forecasts is not None else None
    try:
        report = grade(
            forecasters, args.start, end,
            market=lambda start, finish: frame,
            forecasts=store,
            horizon_hours=args.horizon,
            quantiles=quantiles,
            spike_top=args.spike_top,
            forecast_horizon=dt.timedelta(hours=args.forecast_horizon),
        )
    except (PolicyError, ValueError) as e:
        print(f"harness: error: {e}", file=sys.stderr)
        return 2
    out = args.out or runs_dir() / f"forecast-{args.start}_{end}"
    text = publish(report, out)
    sys.stdout.write(text)
    print(f"Wrote {out / 'leaderboard.json'}, {out / 'leaderboard.txt'}, "
          "mae.svg, pinball.svg, horizon.svg, regime.svg")
    return 0


def _build(spec: str, frame, timeout_s: float, fallback: str):
    if spec == "oracle":
        return oracle(frame)
    if spec in BUILTINS:
        return BUILTINS[spec]()
    return ExternalForecaster(shlex.split(spec), timeout_s=timeout_s, fallback=fallback)


def _quantiles(text: str) -> tuple[float, ...]:
    try:
        levels = tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise PolicyError(f"--quantiles expects comma-separated numbers, got {text!r}") from None
    if not levels or any(not 0 < q < 1 for q in levels):
        raise PolicyError(f"--quantiles must be between 0 and 1, got {text!r}")
    if not any(abs(q - 0.5) < 1e-9 for q in levels):
        raise PolicyError("--quantiles must include 0.5")
    return levels
