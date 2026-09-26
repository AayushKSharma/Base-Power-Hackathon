"""Base-actual dataset: what Base's Aggregate Load Resources did, per SCED run.

Built from the 60-Day SCED Disclosure (NP3-965-ER) load-resource table, stored
next to the market dataset (table `base_actual`, one Parquet file per operating
day) and read offline through `load_base_actual`.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from harness.base_actual.source import LOAD_RESOURCES, DisclosureRoute, latest_published_day
from harness.market import MarketDataMissing
from harness.market.catalog import CPT, RTC_B_START
from harness.market.raw import RawCache
from harness.market.sources import MisClient
from harness.market.store import MarketStore
from harness.paths import data_dir

log = logging.getLogger(__name__)

TABLE = "base_actual"

DateLike = dt.date | str
PathLike = Path | str

# Stored column -> NP3-965-ER column. All quantities are MW.
MEASURES = {
    "max_power_consumption_mw": "Max Power Consumption",
    "low_power_consumption_mw": "Low Power Consumption",
    "real_power_consumption_mw": "Real Power Consumption",
    "base_point_mw": "Base Point",
    "as_capability_ecrs_mw": "AS Capability ECRS",
    "as_capability_nspin_mw": "AS Capability NSPIN",
    "as_award_ecrs_mw": "AS Awards ECRS",
    "as_award_nspin_mw": "AS Awards NSPIN",
    "self_provided_ecrs_mw": "Self Provided ECRS",
}
# An empty award cell means no award in that hour.
AWARDS = ("as_award_ecrs_mw", "as_award_nspin_mw")


def _day(d: DateLike) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(d)


def _days(start: DateLike, end: DateLike) -> list[dt.date]:
    first, last = _day(start), _day(end)
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def default_raw_dir() -> Path:
    return data_dir() / "raw"


def default_store_dir() -> Path:
    return data_dir() / "market"


def _sced_times_utc(rows: pd.DataFrame) -> pd.Series:
    """SCED run times to UTC; ERCOT flags the second pass through the repeated
    fall-back hour with 'Y'."""
    wall = pd.to_datetime(rows["SCED Time Stamp"], format="%m/%d/%Y %H:%M:%S")
    ambiguous = (rows["Repeated Hour Flag"].fillna("N").str.upper() != "Y").to_numpy()
    return wall.dt.tz_localize(CPT, ambiguous=ambiguous, nonexistent="NaT").dt.tz_convert("UTC")


def normalize_day(raw: RawCache, day: dt.date) -> pd.DataFrame | None:
    """One row per (SCED run, ALR), with the stored columns and derived fields."""
    rows = raw.read(LOAD_RESOURCES, day)
    if rows is None:
        return None
    sced = _sced_times_utc(rows)
    out = pd.DataFrame({
        "sced_time_utc": sced,
        "sced_time_cpt": sced.dt.tz_convert(CPT),
        # The 5-minute market interval the run started in (join key to harness.market).
        "interval_start_utc": sced.dt.floor("5min"),
        "operating_day": pd.Timestamp(day),
        "repeated_hour": rows["Repeated Hour Flag"].eq("Y"),
        "qse": rows["QSE"],
        "dme": rows["DME"],
        "resource": rows["Resource Name"],
        "status": rows["Telemetered Resource Status"],
    })
    for col, source in MEASURES.items():
        out[col] = pd.to_numeric(rows[source], errors="coerce").astype(float)
    for col in AWARDS:
        out[col] = out[col].fillna(0.0)
    out["flexible_mw"] = out["real_power_consumption_mw"] - out["low_power_consumption_mw"]
    out["deviation_mw"] = out["real_power_consumption_mw"] - out["base_point_mw"]
    out = out.dropna(subset=["sced_time_utc"])
    return out.sort_values(["sced_time_utc", "resource"]).reset_index(drop=True)


@dataclass
class BaseActualBuild:
    built: list[dt.date] = field(default_factory=list)
    # Operating days whose disclosure is not due yet (less than 60 days old).
    unpublished: list[dt.date] = field(default_factory=list)
    # Published days no document on MIS could supply.
    unavailable: list[dt.date] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_base_actual(
    start: DateLike,
    end: DateLike,
    *,
    raw_dir: PathLike | None = None,
    store_dir: PathLike | None = None,
    fetch: bool = True,
    refetch: bool = False,
    today: dt.date | None = None,
    route: DisclosureRoute | None = None,
) -> BaseActualBuild:
    """Fetch what the raw cache lacks for [start, end], then (re)normalize those days.

    Days less than 60 days old are reported as unpublished, not errors.
    """
    days = _days(start, end)
    if days and days[0] < RTC_B_START:
        raise ValueError(f"the Base-actual dataset starts at RTC+B go-live, {RTC_B_START}; got {days[0]}")
    raw = RawCache(raw_dir or default_raw_dir())
    store = MarketStore(store_dir or default_store_dir())
    today = today or pd.Timestamp.now(tz=CPT).date()
    result = BaseActualBuild()
    published = [d for d in days if d <= latest_published_day(today)]
    result.unpublished = [d for d in days if d not in published]

    if fetch:
        missing = [d for d in published if refetch or not raw.has(LOAD_RESOURCES, d)]
        try:
            fetched = (route or DisclosureRoute(MisClient())).fetch(LOAD_RESOURCES, missing)
        except Exception as exc:  # report it; days already cached still build
            result.errors.append(f"{LOAD_RESOURCES.id}: {exc}")
            log.warning("%s fetch failed: %s", LOAD_RESOURCES.id, exc)
            fetched = {}
        for day, (rows, meta) in sorted(fetched.items()):
            raw.write(LOAD_RESOURCES, day, rows, meta)

    for day in published:
        df = normalize_day(raw, day)
        if df is None:
            result.unavailable.append(day)
            continue
        store.write(TABLE, day, df)
        result.built.append(day)
    return result


def load_base_actual(
    start: DateLike,
    end: DateLike,
    *,
    resources: list[str] | None = None,
    qse: str | None = None,
    store_dir: PathLike | None = None,
) -> pd.DataFrame:
    """ALR rows for operating days [start, end] (CPT), optionally only the given
    resources or one QSE (Base's is harness.base_actual.BASE_QSE).

    Raises MarketDataMissing if any day has not been ingested. ERCOT publishes a
    day 60 days after it, so recent days never are.
    """
    store = MarketStore(store_dir or default_store_dir())
    days = _days(start, end)
    missing = sorted(set(days) - set(store.days(TABLE)))
    if missing:
        raise MarketDataMissing(
            f"Base-actual data in {store.root} is missing {len(missing)} day(s) between "
            f"{missing[0]} and {missing[-1]}. ERCOT publishes a day 60 days later; ingest "
            f"published days with: python -m harness.base_actual build --start {missing[0]} --end {missing[-1]}"
        )
    rows = store.read(TABLE, days)
    if resources is not None:
        rows = rows[rows["resource"].isin(resources)]
    if qse is not None:
        rows = rows[rows["qse"] == qse]
    return rows.reset_index(drop=True)
