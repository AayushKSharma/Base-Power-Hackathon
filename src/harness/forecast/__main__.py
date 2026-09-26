"""python -m harness.forecast {build,report}

    build   fetch what the raw cache lacks, normalize, write the coverage report
    report  print the coverage report of the built store
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import pandas as pd

from harness.forecast.coverage import coverage_report
from harness.forecast.dataset import build_forecasts, default_raw_dir, default_store_dir
from harness.forecast.store import ForecastStore
from harness.market.catalog import CPT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.forecast", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=None, help=f"raw cache (default {default_raw_dir()})")
    parser.add_argument("--store-dir", default=None, help=f"forecast store (default {default_store_dir()})")
    sub = parser.add_subparsers(dest="command", required=True)

    today = pd.Timestamp.now(tz=CPT).date()
    build = sub.add_parser("build", help="fetch and normalize forecast vintages for a posted-date range")
    build.add_argument("--start", type=dt.date.fromisoformat, default=today - dt.timedelta(days=7),
                       help="first posted date, CPT (default 7 days ago; MIS keeps about that long without an API key)")
    build.add_argument("--end", type=dt.date.fromisoformat, default=today,
                       help="last posted date, CPT (default today)")
    build.add_argument("--no-fetch", action="store_true", help="normalize from the raw cache only (offline)")
    build.add_argument("--refetch", action="store_true", help="download vintages again even if cached")

    report = sub.add_parser("report", help="print the coverage report")
    report.add_argument("--start", type=dt.date.fromisoformat, default=None)
    report.add_argument("--end", type=dt.date.fromisoformat, default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    raw_dir = args.raw_dir or default_raw_dir()
    store_dir = args.store_dir or default_store_dir()

    if args.command == "build":
        result = build_forecasts(args.start, args.end, raw_dir=raw_dir, store_dir=store_dir,
                                 fetch=not args.no_fetch, refetch=args.refetch)
        for error in result.report.errors:
            logging.warning("fetch error: %s", error)
        sys.stdout.write(result.report.to_markdown())
        return 0

    first = args.start or dt.date.min
    last = args.end or dt.date.max
    # An unbounded report still has to name a window. Use the dates that are stored.
    store = ForecastStore(store_dir)
    if args.start is None or args.end is None:
        stored = _stored_days(store)
        if not stored:
            print("harness.forecast: error: no forecast vintages stored", file=sys.stderr)
            return 1
        first = args.start or stored[0]
        last = args.end or stored[-1]
    sys.stdout.write(coverage_report(store, first, last).to_markdown())
    return 0


def _stored_days(store: ForecastStore) -> list[dt.date]:
    days: set[dt.date] = set()
    for input_id in store.inputs_on_disk():
        days.update(store.days(input_id))
    return sorted(days)


if __name__ == "__main__":
    raise SystemExit(main())
