"""python -m harness.market {build,report}

    build   fetch what the raw cache lacks, normalize, write the quality report
    report  print the quality report of the built dataset
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from harness.market.catalog import RTC_B_START
from harness.market.dataset import (
    build_dataset,
    default_raw_dir,
    default_store_dir,
    last_complete_day,
    quality_report,
)
from harness.market.scarcity import PriceThresholdScarcity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.market", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=None, help=f"raw cache (default {default_raw_dir()})")
    parser.add_argument("--store-dir", default=None, help=f"dataset (default {default_store_dir()})")
    parser.add_argument("--scarcity-percentile", type=float, default=0.99,
                        help="default scarcity proxy: percentile of RT MCPC since RTC+B (default 0.99)")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="fetch and normalize a date range")
    build.add_argument("--start", type=dt.date.fromisoformat, default=RTC_B_START, help=f"first operating day (default {RTC_B_START})")
    build.add_argument("--end", type=dt.date.fromisoformat, default=None, help="last operating day (default yesterday, CPT)")
    build.add_argument("--no-fetch", action="store_true", help="normalize from the raw cache only (offline)")
    build.add_argument("--refetch", action="store_true", help="download raw files again even if cached")

    report = sub.add_parser("report", help="print the quality report")
    report.add_argument("--start", type=dt.date.fromisoformat, default=None)
    report.add_argument("--end", type=dt.date.fromisoformat, default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    raw_dir = args.raw_dir or default_raw_dir()
    store_dir = args.store_dir or default_store_dir()
    scarcity = PriceThresholdScarcity(percentile=args.scarcity_percentile)

    if args.command == "build":
        result = build_dataset(args.start, args.end or last_complete_day(), raw_dir=raw_dir,
                               store_dir=store_dir, fetch=not args.no_fetch, refetch=args.refetch,
                               scarcity=scarcity)
        if result.fetch is not None:
            for report_id, days in result.fetch.uncovered.items():
                if days:
                    logging.warning("%s: no source for %d day(s), %s to %s",
                                    report_id, len(days), days[0], days[-1])
            for error in result.fetch.errors:
                logging.warning("fetch error: %s", error)
        report_text = result.report.to_markdown()
    else:
        report_text = quality_report(args.start, args.end, store_dir=store_dir, raw_dir=raw_dir,
                                     scarcity=scarcity).to_markdown()
    sys.stdout.write(report_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
