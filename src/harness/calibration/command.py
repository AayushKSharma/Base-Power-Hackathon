"""python -m harness.calibration — write a quantile-mock share table.

Reads an ingested Base-actual store (no network) and writes shares.csv in the
layout scenarios already load: month, hour, P10, P25, P50, P75, P90. It also
writes provenance.json (source range, resources, empty-bucket fallbacks),
bands.svg (the hour-of-day quantile bands) and scenario.yaml, which selects
shares.csv. Fleet, failures and products in that scenario match
scenarios/baseline.yaml, so the placeholder shares stay available for comparison.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import yaml

from harness.base_actual import BASE_QSE, load_base_actual
from harness.calibration.chart import bands_svg
from harness.calibration.quantiles import quantile_shares

BASELINE = Path(__file__).resolve().parents[3] / "scenarios" / "baseline.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.calibration", description=__doc__)
    parser.add_argument("--store-dir", required=True, help="market store that holds the base_actual table")
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True, help="first operating day")
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True, help="last operating day")
    parser.add_argument("--out", required=True, help="directory for shares.csv")
    args = parser.parse_args(argv)

    rows = load_base_actual(args.start, args.end, qse=BASE_QSE, store_dir=args.store_dir)
    calibrated = quantile_shares(rows, BASE_QSE)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    calibrated.shares.to_csv(out / "shares.csv", index=False)
    provenance = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "qse": BASE_QSE,
        "resources": sorted(str(name) for name in rows["resource"].unique()),
        "clipped_runs": calibrated.clipped_runs,
        "skipped_runs": calibrated.skipped_runs,
        "fallback_buckets": calibrated.fallback_buckets,
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (out / "scenario.yaml").write_text(_scenario_text())
    (out / "bands.svg").write_text(bands_svg(calibrated.hour_of_day))
    return 0


def _scenario_text() -> str:
    """baseline.yaml, with its placeholder shares replaced by shares.csv."""
    data = yaml.safe_load(BASELINE.read_text())
    data["fleet"]["quantile_mock"]["shares"] = "shares.csv"
    return (
        "# Calibrated quantile shares (shares.csv). Provenance is in provenance.json.\n"
        "# Fleet, failures and products match scenarios/baseline.yaml so its placeholder\n"
        "# shares stay available for comparison.\n"
        + yaml.safe_dump(data, sort_keys=False)
    )
