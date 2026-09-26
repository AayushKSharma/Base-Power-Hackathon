"""Build and query the forecast-input store.

`as_of` is the only way a policy reads forecasts. It returns, per input, the
rows of the latest vintage whose posted time is at or before the decision time.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from harness.forecast.catalog import INPUTS, ForecastInput
from harness.forecast.coverage import CoverageReport, coverage_report
from harness.forecast.parse import empty_frame, parse_vintage
from harness.forecast.raw import CachedVintage, ForecastRaw
from harness.forecast.sources import Route, default_routes
from harness.forecast.store import ForecastStore
from harness.market.catalog import CPT
from harness.paths import data_dir

log = logging.getLogger(__name__)

REPORT_FILE = "coverage.md"

DateLike = dt.date | str
PathLike = Path | str


def default_raw_dir() -> Path:
    return data_dir() / "forecast-raw"


def default_store_dir() -> Path:
    return data_dir() / "forecasts"


def _date(value: DateLike) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


@dataclass
class BuildResult:
    start: dt.date
    end: dt.date
    vintages: int
    report: CoverageReport


def build_forecasts(
    start: DateLike,
    end: DateLike,
    *,
    raw_dir: PathLike | None = None,
    store_dir: PathLike | None = None,
    fetch: bool = True,
    refetch: bool = False,
    routes: list[Route] | None = None,
) -> BuildResult:
    """Fill the raw cache for posted dates [start, end], then rewrite those partitions.

    Rebuilding a range from the same raw files rewrites the same rows. With
    `fetch=False` nothing touches the network.
    """
    first, last = _date(start), _date(end)
    if first > last:
        raise ValueError(f"start {first} is after end {last}")
    raw = ForecastRaw(raw_dir or default_raw_dir())
    store = ForecastStore(store_dir or default_store_dir())
    errors: list[str] = []
    if fetch:
        chosen = routes if routes is not None else default_routes(raw, refetch=refetch)
        errors.extend(_fetch(raw, chosen, first, last, refetch=refetch))

    written = 0
    for spec in INPUTS:
        written += _normalize(raw, store, spec, first, last, errors)
    report = coverage_report(store, first, last, errors=errors)
    text = report.to_markdown()
    target = store.root / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return BuildResult(first, last, written, report)


def _fetch(raw: ForecastRaw, routes: list[Route], start: dt.date, end: dt.date, *, refetch: bool) -> list[str]:
    errors: list[str] = []
    for spec in INPUTS:
        for route in routes:
            try:
                fetched = route.fetch(spec, start, end)
            except Exception as exc:  # one route must not stop the others
                errors.append(f"{spec.report_id} via {route.name}: {exc}")
                log.warning("%s via %s failed: %s", spec.report_id, route.name, exc)
                continue
            for vintage in fetched:
                if raw.has(spec.report_id, vintage.vintage_id) and not refetch:
                    continue
                raw.write(spec.report_id, vintage.vintage_id, vintage.rows, vintage.meta)
    for route in routes:
        errors.extend(route.errors)
    return errors


def _normalize(
    raw: ForecastRaw,
    store: ForecastStore,
    spec: ForecastInput,
    start: dt.date,
    end: dt.date,
    errors: list[str],
) -> int:
    frames: list[pd.DataFrame] = []
    vintages: list[CachedVintage] = raw.read_report(spec.report_id, start, end)
    for vintage in sorted(vintages, key=lambda item: item.vintage_id):
        try:
            frames.append(parse_vintage(spec, vintage.rows, vintage.posted_time))
        except Exception as exc:
            errors.append(f"{spec.report_id} {vintage.vintage_id}: {exc}")
            log.warning("could not parse %s %s: %s", spec.report_id, vintage.vintage_id, exc)
    combined = empty_frame() if not frames else pd.concat(frames, ignore_index=True)
    if not combined.empty:
        # Two documents that share a posted time both stay. Only rows repeated inside
        # one document were dropped, in parse_vintage.
        combined = combined.sort_values(
            ["posted_time", "valid_time", "series"], kind="stable",
        ).reset_index(drop=True)
    rewritten: set[dt.date] = set()
    if not combined.empty:
        posted = pd.to_datetime(combined["posted_time"], utc=True)
        combined = combined.assign(_day=posted.dt.tz_convert(CPT).dt.strftime("%Y-%m-%d"))
        for day_text, part in combined.groupby("_day", sort=True):
            day = dt.date.fromisoformat(str(day_text))
            store.write(spec.id, day, part.drop(columns="_day").reset_index(drop=True))
            rewritten.add(day)
    for day in store.days(spec.id):
        if start <= day <= end and day not in rewritten:
            store.remove(spec.id, day)
    return len(vintages)


def load_forecasts(input_id: str, *, store_dir: PathLike | None = None) -> pd.DataFrame:
    """Every stored row of one input. Tests and the coverage report read through this."""
    return ForecastStore(store_dir or default_store_dir()).load(input_id)
