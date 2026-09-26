"""python -m harness.base_actual {build,summary}

    build    ingest ALR rows of the 60-Day SCED Disclosure (NP3-965-ER) for a date range
    summary  per resource per day: mean flexible MW, max ECRS/Non-Spin awards,
             mean |deviation| and dispatch-down events
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import pandas as pd

from harness.base_actual.analysis import DEFAULT_DISPATCH_THRESHOLD_MW, daily_summary, dispatch_down_events
from harness.base_actual.dataset import TABLE, build_base_actual, default_raw_dir, default_store_dir, load_base_actual
from harness.base_actual.source import BASE_QSE, latest_published_day
from harness.market import MarketDataMissing
from harness.market.catalog import CPT, RTC_B_START
from harness.market.store import MarketStore


def _ranges(days: list[dt.date]) -> str:
    if not days:
        return "none"
    runs: list[list[dt.date]] = []
    for d in days:
        if runs and (d - runs[-1][-1]).days == 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]} to {r[-1]}" for r in runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.base_actual", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=None, help=f"raw cache (default {default_raw_dir()})")
    parser.add_argument("--store-dir", default=None, help=f"dataset (default {default_store_dir()})")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="ingest a date range")
    build.add_argument("--start", type=dt.date.fromisoformat, default=RTC_B_START,
                       help=f"first operating day (default {RTC_B_START})")
    build.add_argument("--end", type=dt.date.fromisoformat, default=None,
                       help="last operating day (default: the latest published, 60 days ago)")
    build.add_argument("--no-fetch", action="store_true", help="normalize from the raw cache only (offline)")
    build.add_argument("--refetch", action="store_true", help="download again even if cached")

    summary = sub.add_parser("summary", help="print per-resource daily stats")
    summary.add_argument("--start", type=dt.date.fromisoformat, default=None, help="default: first ingested day")
    summary.add_argument("--end", type=dt.date.fromisoformat, default=None, help="default: last ingested day")
    summary.add_argument("--all", action="store_true", help=f"every ALR, not just Base's QSE {BASE_QSE}")
    summary.add_argument("--threshold", type=float, default=DEFAULT_DISPATCH_THRESHOLD_MW,
                         help=f"dispatch-down threshold, MW (default {DEFAULT_DISPATCH_THRESHOLD_MW})")
    summary.add_argument("--events", action="store_true", help="also list each dispatch-down event")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.command == "build":
        end = args.end or latest_published_day(pd.Timestamp.now(tz=CPT).date())
        result = build_base_actual(args.start, end, raw_dir=args.raw_dir, store_dir=args.store_dir,
                                   fetch=not args.no_fetch, refetch=args.refetch)
        print(f"built:       {len(result.built)} day(s): {_ranges(result.built)}")
        print(f"unpublished: {len(result.unpublished)} day(s): {_ranges(result.unpublished)} "
              "(ERCOT publishes 60 days after the operating day)")
        print(f"unavailable: {len(result.unavailable)} day(s): {_ranges(result.unavailable)}")
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1 if result.errors else 0

    ingested = MarketStore(args.store_dir or default_store_dir()).days(TABLE)
    days = [d for d in ingested if (args.start or dt.date.min) <= d <= (args.end or dt.date.max)]
    if not days:
        print("no Base-actual data ingested for that range; run: python -m harness.base_actual build",
              file=sys.stderr)
        return 1
    try:
        rows = pd.concat(load_base_actual(d, d, store_dir=args.store_dir, qse=None if args.all else BASE_QSE)
                         for d in days)
    except MarketDataMissing as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with pd.option_context("display.width", 200, "display.max_rows", None):
        print(daily_summary(rows, args.threshold).round(2).to_string())
        if args.events:
            print()
            print(dispatch_down_events(rows, args.threshold).round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
