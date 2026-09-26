"""Build and load the market dataset: the public interface of harness.market.

Everything downstream (scoring, backtest, run farm, live replay) reads the
dataset through `load_intervals` / `load_asdc`, which never touch the network.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from harness.market.catalog import CPT, RTC_B_START, scarce_column
from harness.market.normalize import ASDC_COLUMNS, normalize_asdc, normalize_day
from harness.market.quality import QualityReport, build_report, write_report
from harness.market.raw import RawCache
from harness.market.scarcity import DEFAULT_SCARCITY, History, ScarcityProxy
from harness.market.sources import FetchResult, Route, default_routes, fetch_missing
from harness.market.store import MarketStore
from harness.paths import data_dir

log = logging.getLogger(__name__)

INTERVALS = "intervals"
DEMAND_CURVES = "asdc"
REPORT_FILE = "quality_report.md"

DateLike = dt.date | str
PathLike = Path | str


class MarketDataMissing(LookupError):
    """The market dataset has not been built for the requested days."""


def default_raw_dir() -> Path:
    return data_dir() / "raw"


def default_store_dir() -> Path:
    return data_dir() / "market"


def last_complete_day() -> dt.date:
    """Yesterday in CPT: the latest operating day whose data can be complete."""
    return pd.Timestamp.now(tz=CPT).date() - dt.timedelta(days=1)


def _as_date(d: DateLike) -> dt.date:
    if isinstance(d, dt.datetime):  # includes pd.Timestamp
        return d.date()
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(d)


def _days(start: DateLike, end: DateLike) -> list[dt.date]:
    first, last = _as_date(start), _as_date(end)
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


@dataclass
class BuildResult:
    days: list[dt.date]
    fetch: FetchResult | None
    report: QualityReport


def build_dataset(
    start: DateLike,
    end: DateLike,
    *,
    raw_dir: PathLike | None = None,
    store_dir: PathLike | None = None,
    fetch: bool = True,
    refetch: bool = False,
    routes: list[Route] | None = None,
    scarcity: ScarcityProxy = DEFAULT_SCARCITY,
) -> BuildResult:
    """Fetch what the raw cache lacks for [start, end], then (re)normalize those days.

    Rebuilding a range rewrites the same day files, so it is idempotent. With
    `fetch=False` the build uses only the raw cache and never touches the network.
    """
    first, last = _as_date(start), _as_date(end)
    if first < RTC_B_START:
        raise ValueError(f"the market dataset starts at RTC+B go-live, {RTC_B_START}; got {first}")
    if first > last:
        raise ValueError(f"start {first} is after end {last}")
    raw = RawCache(raw_dir or default_raw_dir())
    store = MarketStore(store_dir or default_store_dir())

    fetched: FetchResult | None = None
    if fetch:
        if last > last_complete_day():
            last = last_complete_day()
            log.warning("only complete operating days can be fetched; ending at %s", last)
        days = _days(first, last)
        fetched = fetch_missing(raw, days, routes if routes is not None else default_routes(raw),
                                refetch=refetch)
    days = _days(first, last)

    for day in days:
        store.write(INTERVALS, day, normalize_day(raw, day))
        asdc = normalize_asdc(raw, day)
        if asdc is None:
            store.remove(DEMAND_CURVES, day)
        else:
            store.write(DEMAND_CURVES, day, asdc)

    report = quality_report(store_dir=store.root, raw_dir=raw.root, scarcity=scarcity)
    write_report(report, store.root / REPORT_FILE)
    return BuildResult(days, fetched, report)


def _built_days(store: MarketStore, start: DateLike, end: DateLike) -> list[dt.date]:
    days = _days(start, end)
    missing = sorted(set(days) - set(store.days(INTERVALS)))
    if missing:
        raise MarketDataMissing(
            f"market dataset in {store.root} is missing {len(missing)} day(s) between "
            f"{missing[0]} and {missing[-1]}; build them with: "
            f"python -m harness.market build --start {missing[0]} --end {missing[-1]}"
        )
    return days


def _history(store: MarketStore) -> History:
    def read(columns: list[str]) -> pd.DataFrame:
        return store.read(INTERVALS, store.days(INTERVALS), columns=columns)

    return read


def load_intervals(
    start: DateLike,
    end: DateLike,
    *,
    store_dir: PathLike | None = None,
    scarcity: ScarcityProxy = DEFAULT_SCARCITY,
) -> pd.DataFrame:
    """The 5-minute interval table for operating days [start, end] (CPT), indexed by
    `interval_start_utc`. Columns are documented in data/README.md.

    Raises MarketDataMissing (a LookupError) if any day in the range has not been built.
    """
    store = MarketStore(store_dir or default_store_dir())
    return _read_intervals(store, _built_days(store, start, end), scarcity)


def _read_intervals(store: MarketStore, days: list[dt.date], scarcity: ScarcityProxy) -> pd.DataFrame:
    intervals = store.read(INTERVALS, days)
    flags = scarcity.flags(intervals, _history(store))
    for product in flags.columns:
        intervals[scarce_column(product)] = flags[product].astype("boolean")
    return intervals


def load_asdc(start: DateLike, end: DateLike, *, store_dir: PathLike | None = None) -> pd.DataFrame:
    """Hourly ancillary-service demand curves (NP4-212-CD) for the built days that have them."""
    store = MarketStore(store_dir or default_store_dir())
    days = set(_built_days(store, start, end)) & set(store.days(DEMAND_CURVES))
    if not days:
        return pd.DataFrame(columns=ASDC_COLUMNS)
    return store.read(DEMAND_CURVES, sorted(days)).reset_index(drop=True)


def quality_report(
    start: DateLike | None = None,
    end: DateLike | None = None,
    *,
    store_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    scarcity: ScarcityProxy = DEFAULT_SCARCITY,
) -> QualityReport:
    """Coverage, gaps, uncovered days, price ranges and scarcity counts for the
    built days in [start, end] (default: every built day)."""
    store = MarketStore(store_dir or default_store_dir())
    first = _as_date(start) if start else dt.date.min
    last = _as_date(end) if end else dt.date.max
    days = [d for d in store.days(INTERVALS) if first <= d <= last]
    if not days:
        raise MarketDataMissing(f"no built market data in {store.root} for that range; "
                          "run: python -m harness.market build")
    intervals = _read_intervals(store, days, scarcity)
    # Proxies that are price thresholds can say what their thresholds are.
    threshold_values = getattr(scarcity, "threshold_values", None)
    thresholds = threshold_values(_history(store)) if threshold_values else None
    raw = RawCache(raw_dir) if raw_dir is not None else None
    asdc_days = [d for d in store.days(DEMAND_CURVES) if first <= d <= last]
    return build_report(intervals, asdc_days, thresholds, raw)
