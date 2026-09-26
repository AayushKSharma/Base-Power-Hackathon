"""Record the raw ERCOT fixture files the market dataset tests build from.

    .venv/bin/python scripts/record_fixtures.py

Copies raw day files from the local raw cache (fetching any that are missing)
into tests/fixtures/raw, in the same layout, so tests build with no network:

- 2026-09-10  normal day, including the ASDC file
- 2026-03-08  spring-forward day (23 hours)
- 2026-03-09  the day after, so harness runs have a contiguous two-day range
              across the DST change
- 2026-08-26  price spike (ECRS and Non-Spin MCPC above $800/MW-h)
- 2026-03-19  NP6-331-CD file deliberately left out; NP6-332-CD also has a real
              SCED gap at 10:35-10:45
- 2026-11-01  SYNTHETIC fall-back day (25 hours), made from 2026-09-10 because
              no post-RTC+B fall-back day has happened yet. The repeated hour's
              second copy has RepeatedHourFlag/DSTFlag = Y and prices +100.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd

from harness.market.catalog import ASDC, MCPC_15MIN, REPORTS, Report
from harness.market.raw import RawCache
from harness.market.sources import default_routes, fetch_missing

ROOT = Path(__file__).resolve().parents[1]

FIXTURES = RawCache(ROOT / "tests" / "fixtures" / "raw")
CACHE = RawCache(ROOT / "data" / "raw")

NORMAL = dt.date(2026, 9, 10)
SPRING_FORWARD = dt.date(2026, 3, 8)
AFTER_SPRING_FORWARD = dt.date(2026, 3, 9)
SPIKE = dt.date(2026, 8, 26)
MISSING_FILE = dt.date(2026, 3, 19)
FALL_BACK = dt.date(2026, 11, 1)

RECORDED: dict[dt.date, tuple[Report, ...]] = {
    NORMAL: REPORTS,
    SPRING_FORWARD: tuple(r for r in REPORTS if r is not ASDC),
    AFTER_SPRING_FORWARD: tuple(r for r in REPORTS if r is not ASDC),
    SPIKE: tuple(r for r in REPORTS if r is not ASDC),
    MISSING_FILE: tuple(r for r in REPORTS if r not in (ASDC, MCPC_15MIN)),
}

PRICE_COLUMNS = {"CappedMCPC", "UncappedMCPC", "MCPC", "SettlementPointPrice"}


def copy_recorded() -> None:
    days = sorted(RECORDED)
    fetch_missing(CACHE, days, default_routes(CACHE))
    for day, reports in RECORDED.items():
        for report in reports:
            src = CACHE.path(report, day)
            if not src.exists():
                raise SystemExit(f"{report.id} {day} is not in the raw cache")
            dst = FIXTURES.path(report, day)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            shutil.copyfile(CACHE.meta_path(report, day), FIXTURES.meta_path(report, day))


def _repeat_hour(report: Report, rows: pd.DataFrame) -> pd.DataFrame:
    """Add the second (standard-time) copy of 01:00-02:00 local."""
    rows = rows.copy()
    if report.day_column == "SCEDTimestamp":
        in_hour = rows["SCEDTimestamp"].str.slice(11, 13) == "01"
        flag = "RepeatedHourFlag"
    elif "DeliveryHour" in rows:
        in_hour = rows["DeliveryHour"] == "2"
        flag = "RepeatedHourFlag" if "RepeatedHourFlag" in rows else "DSTFlag"
    else:
        in_hour = rows["HourEnding"] == "02:00"
        flag = "DSTFlag"
    second = rows[in_hour].copy()
    second[flag] = "Y"
    for col in PRICE_COLUMNS & set(second.columns):
        second[col] = (pd.to_numeric(second[col]) + 100).round(2).astype(str)
    # The second copy follows the first in time, as in ERCOT's files.
    last_first = rows.index[in_hour].max()
    return pd.concat([rows.loc[:last_first], second, rows.loc[last_first + 1 :]], ignore_index=True)


def make_fall_back() -> None:
    old, new = NORMAL.strftime("%m/%d/%Y"), FALL_BACK.strftime("%m/%d/%Y")
    for report in RECORDED[SPIKE]:
        rows = FIXTURES.read(report, NORMAL)
        assert rows is not None
        rows[report.day_column] = rows[report.day_column].str.replace(old, new, regex=False)
        FIXTURES.write(report, FALL_BACK, _repeat_hour(report, rows),
                       {"route": "synthetic", "derived_from": NORMAL.isoformat(),
                        "note": "fall-back day built from a real day; not ERCOT data"})


if __name__ == "__main__":
    if FIXTURES.root.exists():
        shutil.rmtree(FIXTURES.root)
    copy_recorded()
    make_fall_back()
    print(f"fixtures written to {FIXTURES.root}")
