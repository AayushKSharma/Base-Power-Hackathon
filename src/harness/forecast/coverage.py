"""Coverage of the forecast-input store: posted-time range and gaps, per input."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from harness.forecast.catalog import DAILY, FIVE_MIN, HOURLY, INPUTS, ForecastInput
from harness.forecast.sources import ApiForecastRoute
from harness.forecast.store import ForecastStore
from harness.market.catalog import CPT

# A gap is successive postings farther apart than the report's cadence, plus slack
# for a document that lands a few minutes off the hour or the 5-minute RTD run.
_GAP_AFTER = {
    HOURLY: pd.Timedelta(minutes=90),
    DAILY: pd.Timedelta(hours=36),
    FIVE_MIN: pd.Timedelta(minutes=10),
}


@dataclass(frozen=True)
class InputCoverage:
    spec: ForecastInput
    first_posted: pd.Timestamp | None
    last_posted: pd.Timestamp | None
    vintages: int
    gaps: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    # Posted dates inside the requested window with no vintage, as inclusive ranges.
    uncovered: tuple[tuple[dt.date, dt.date], ...]


@dataclass
class CoverageReport:
    start: dt.date
    end: dt.date
    inputs: list[InputCoverage]
    api_credentials: bool
    errors: tuple[str, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Forecast-input coverage",
            "",
            f"Requested posted dates {self.start.isoformat()} to {self.end.isoformat()} (Central Prevailing Time).",
            "",
        ]
        if self.api_credentials:
            lines.append("ERCOT API credentials are set, so the archive's postDatetime history was requested.")
        else:
            lines.append(
                "ERCOT API credentials are not set "
                "(`ERCOT_API_USERNAME`, `ERCOT_API_PASSWORD`, `ERCOT_PUBLIC_API_SUBSCRIPTION_KEY`). "
                "Only the documents MIS still keeps were fetched. Older posted dates are gaps."
            )
        lines.append("")
        if self.errors:
            lines.append("Fetch errors:")
            lines.extend(f"- {error}" for error in self.errors)
            lines.append("")
        lines.append("| Input | Report | Posted from | Posted to | Vintages | Gaps | Uncovered dates |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in self.inputs:
            first = _stamp(item.first_posted)
            last = _stamp(item.last_posted)
            gaps = str(len(item.gaps))
            uncovered = _dates(item.uncovered)
            lines.append(
                f"| `{item.spec.id}` | {item.spec.report_id} | {first} | {last} | {item.vintages} | {gaps} | {uncovered} |"
            )
        lines.append("")
        for item in self.inputs:
            if not item.gaps:
                continue
            lines.append(f"### `{item.spec.id}` gaps")
            lines.append("")
            for after, before in item.gaps:
                lines.append(f"- {after.isoformat()} → {before.isoformat()}")
            lines.append("")
        return "\n".join(lines)


def coverage_report(
    store: ForecastStore,
    start: dt.date,
    end: dt.date,
    *,
    errors: list[str] | None = None,
    api_credentials: bool | None = None,
) -> CoverageReport:
    items = [cover_input(store, spec, start, end) for spec in INPUTS]
    credentials = ApiForecastRoute.available() if api_credentials is None else api_credentials
    return CoverageReport(start, end, items, credentials, tuple(errors or ()))


def cover_input(store: ForecastStore, spec: ForecastInput, start: dt.date, end: dt.date) -> InputCoverage:
    frame = store.load(spec.id)
    if frame.empty:
        return InputCoverage(spec, None, None, 0, (), ((start, end),) if start <= end else ())
    posted = pd.to_datetime(frame["posted_time"], utc=True)
    days = posted.dt.tz_convert(CPT).dt.date
    in_window = frame.loc[(days >= start) & (days <= end)]
    if in_window.empty:
        return InputCoverage(spec, None, None, 0, (), ((start, end),))
    times = pd.to_datetime(in_window["posted_time"], utc=True).drop_duplicates().sort_values()
    limit = _GAP_AFTER[spec.cadence]
    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    previous: pd.Timestamp | None = None
    for current in times:
        stamp = pd.Timestamp(current)
        if previous is not None and stamp - previous > limit:
            gaps.append((previous, stamp))
        previous = stamp
    covered_days = set(pd.to_datetime(in_window["posted_time"], utc=True).dt.tz_convert(CPT).dt.date)
    return InputCoverage(
        spec,
        pd.Timestamp(times.iloc[0]),
        pd.Timestamp(times.iloc[-1]),
        int(times.size),
        tuple(gaps),
        tuple(_missing_ranges(start, end, covered_days)),
    )


def _missing_ranges(start: dt.date, end: dt.date, covered: set[dt.date]) -> list[tuple[dt.date, dt.date]]:
    ranges: list[tuple[dt.date, dt.date]] = []
    day = start
    run_start: dt.date | None = None
    while day <= end:
        if day not in covered:
            run_start = day if run_start is None else run_start
        elif run_start is not None:
            ranges.append((run_start, day - dt.timedelta(days=1)))
            run_start = None
        day += dt.timedelta(days=1)
    if run_start is not None:
        ranges.append((run_start, end))
    return ranges


def _stamp(value: pd.Timestamp | None) -> str:
    return "—" if value is None else pd.Timestamp(value).isoformat()


def _dates(ranges: tuple[tuple[dt.date, dt.date], ...]) -> str:
    if not ranges:
        return "—"
    return ", ".join(a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}" for a, b in ranges)
