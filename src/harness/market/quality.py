"""Data-quality report for the market dataset: what the backtest actually covers."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from harness.market.catalog import (
    CPT,
    FIELDS,
    PRODUCTS,
    Q_GAP,
    Q_NO_SOURCE,
    Q_OK,
    REPORTS,
    Report,
    quality_column,
    scarce_column,
)
from harness.market.raw import RawCache

DayRange = tuple[dt.date, dt.date]
Span = tuple[pd.Timestamp, pd.Timestamp]


def _day_ranges(days: list[dt.date]) -> list[DayRange]:
    ranges: list[DayRange] = []
    for day in sorted(days):
        if ranges and (day - ranges[-1][1]).days == 1:
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges


def _spans(starts: pd.DatetimeIndex) -> list[Span]:
    """Merge consecutive 5-minute interval starts into (start, end) spans in CPT."""
    spans: list[Span] = []
    step = pd.Timedelta(minutes=5)
    for t in starts.sort_values():
        if spans and t == spans[-1][1]:
            spans[-1] = (spans[-1][0], t + step)
        else:
            spans.append((t, t + step))
    return [(a.tz_convert(CPT), b.tz_convert(CPT)) for a, b in spans]


def _fmt_ranges(ranges: list[DayRange]) -> str:
    if not ranges:
        return "none"
    return ", ".join(a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}" for a, b in ranges)


@dataclass
class QualityReport:
    start: dt.date
    end: dt.date
    days: int
    rows: int
    # Per field: report, unit, coverage (share of intervals flagged ok), ok, gap,
    # no_source intervals, and no_source_days.
    fields: pd.DataFrame
    # Per report: day ranges with no source file.
    uncovered: dict[str, list[DayRange]]
    # Per report: CPT spans where the source file exists but some field has no value.
    gaps: dict[str, list[Span]]
    # Per report: (route, first day, last day) runs from the raw cache sidecars.
    routes: dict[str, list[tuple[str, dt.date, dt.date]]]
    # Per product: threshold (when the proxy has one) and scarce interval count.
    scarcity: pd.DataFrame
    # Per price field: min, median, max.
    prices: pd.DataFrame

    def to_markdown(self) -> str:
        lines = [
            "# Market dataset quality report",
            "",
            f"- Operating days: {self.start} to {self.end} ({self.days} days, {self.rows:,} five-minute rows)",
            "- No values are filled: a missing value stays empty and its `q_<field>` flag says why"
            " (`gap`: source file present but no value; `no_source`: no source file for the day).",
            "",
            "## Sources",
            "",
            "| Report | Days with no source file | Routes | Gap spans |",
            "|---|---|---|---|",
        ]
        for report in REPORTS:
            routes = "; ".join(
                f"{r} {_fmt_ranges([(a, b)])}" for r, a, b in self.routes.get(report.id, [])
            ) or "n/a"
            gaps = self.gaps.get(report.id, [])
            lines.append(
                f"| {report.id} | {_fmt_ranges(self.uncovered.get(report.id, []))} | {routes} | {len(gaps)} |"
            )
        lines += [
            "",
            "## Coverage per field",
            "",
            "| Field | Report | Unit | Coverage | Gap intervals | Days with no source |",
            "|---|---|---|---|---|---|",
        ]
        for name, row in self.fields.iterrows():
            lines.append(
                f"| `{name}` | {row['report']} | {row['unit']} | {row['coverage']:.2%} | {row['gap']} "
                f"| {len(row['no_source_days'])} |"
            )
        lines += ["", "## Prices", "", "| Field | Min | Median | Max |", "|---|---|---|---|"]
        for name, row in self.prices.iterrows():
            lines.append(f"| `{name}` | {row['min']:.2f} | {row['median']:.2f} | {row['max']:.2f} |")
        lines += ["", "## Scarcity proxy", "", "| Product | Threshold ($/MW-h) | Scarce intervals |",
                  "|---|---|---|"]
        for product, row in self.scarcity.iterrows():
            threshold = "n/a" if pd.isna(row["threshold"]) else f"{row['threshold']:.2f}"
            lines.append(f"| {product} | {threshold} | {int(row['intervals'])} |")
        worst = sorted(
            ((b - a, rid, a, b) for rid, spans in self.gaps.items() for a, b in spans), reverse=True
        )[:20]
        if worst:
            lines += ["", "## Longest gaps (CPT)", "", "| Report | From | To |", "|---|---|---|"]
            for _, rid, a, b in worst:
                lines.append(f"| {rid} | {a:%Y-%m-%d %H:%M} | {b:%Y-%m-%d %H:%M} |")
        return "\n".join(lines) + "\n"


def _routes(raw: RawCache, report: Report, days: list[dt.date]) -> list[tuple[str, dt.date, dt.date]]:
    runs: list[tuple[str, dt.date, dt.date]] = []
    for day in days:
        meta = raw.meta(report, day)
        if meta is None:
            continue
        route = f"{meta.get('route')} ({meta.get('report', report.id)})"
        if runs and runs[-1][0] == route and (day - runs[-1][2]).days == 1:
            runs[-1] = (route, runs[-1][1], day)
        else:
            runs.append((route, day, day))
    return runs


def build_report(
    intervals: pd.DataFrame,
    asdc_days: list[dt.date],
    thresholds: dict[str, float] | None,
    raw: RawCache | None,
) -> QualityReport:
    days = sorted(pd.to_datetime(intervals["operating_day"]).dt.date.unique())
    day_of = pd.to_datetime(intervals["operating_day"]).dt.date
    field_rows = {}
    for f in FIELDS:
        q = intervals[quality_column(f.name)]
        no_source = sorted(set(day_of[q == Q_NO_SOURCE]))
        field_rows[f.name] = {
            "report": f.report.id,
            "unit": f.unit,
            "coverage": float((q == Q_OK).mean()),
            "ok": int((q == Q_OK).sum()),
            "gap": int((q == Q_GAP).sum()),
            "no_source": int((q == Q_NO_SOURCE).sum()),
            "no_source_days": no_source,
        }
    fields = pd.DataFrame.from_dict(field_rows, orient="index")

    uncovered: dict[str, list[DayRange]] = {}
    gaps: dict[str, list[Span]] = {}
    for report in REPORTS:
        names = [f.name for f in FIELDS if f.report is report]
        if names:
            flags = intervals[[quality_column(n) for n in names]]
            no_source_days = sorted(set(day_of[(flags == Q_NO_SOURCE).all(axis=1)]))
            gap_rows = intervals.index[(flags == Q_GAP).any(axis=1)]
            gaps[report.id] = _spans(pd.DatetimeIndex(gap_rows))
        else:  # the ASDC table
            no_source_days = sorted(set(days) - set(asdc_days))
            gaps[report.id] = []
        uncovered[report.id] = _day_ranges(no_source_days)

    routes = {r.id: _routes(raw, r, days) for r in REPORTS} if raw is not None else {}

    scarcity = pd.DataFrame(
        {
            "threshold": [(thresholds or {}).get(p, float("nan")) for p in PRODUCTS.values()],
            "intervals": [
                int(intervals[scarce_column(p)].sum()) if scarce_column(p) in intervals else 0
                for p in PRODUCTS.values()
            ],
        },
        index=list(PRODUCTS.values()),
    )
    price_fields = [f.name for f in FIELDS if f.unit.startswith("$")]
    prices = intervals[price_fields].agg(["min", "median", "max"]).T

    return QualityReport(
        start=days[0],
        end=days[-1],
        days=len(days),
        rows=len(intervals),
        fields=fields,
        uncovered=uncovered,
        gaps=gaps,
        routes=routes,
        scarcity=scarcity,
        prices=prices,
    )


def write_report(report: QualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown())
