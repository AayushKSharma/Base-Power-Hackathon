"""Raw cache of forecast vintages: one CSV per posted document, plus a sidecar.

The sidecar holds the publication time. Normalization can be rerun without
fetching. Vintages are files, so two postings of the same hour stay two files.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from harness.market.catalog import CPT


def vintage_id(posted_time: pd.Timestamp, doc_id: str) -> str:
    posted = pd.Timestamp(posted_time).tz_convert("UTC")
    stamp = posted.strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in doc_id)
    return f"{stamp}__{safe}"


@dataclass(frozen=True)
class CachedVintage:
    report_id: str
    vintage_id: str
    posted_time: pd.Timestamp
    meta: dict[str, Any]
    rows: pd.DataFrame


class ForecastRaw:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path(self, report_id: str, vintage: str) -> Path:
        return self.root / report_id / f"{vintage}.csv.gz"

    def meta_path(self, report_id: str, vintage: str) -> Path:
        return self.root / report_id / f"{vintage}.meta.json"

    def has(self, report_id: str, vintage: str) -> bool:
        return self.path(report_id, vintage).exists()

    def write(self, report_id: str, vintage: str, rows: pd.DataFrame, meta: dict[str, Any]) -> None:
        path = self.path(report_id, vintage)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = rows.astype(str).copy()
        tmp = path.with_suffix(".tmp")
        text.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0})
        os.replace(tmp, path)
        self.meta_path(report_id, vintage).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    def read_report(self, report_id: str, start: dt.date, end: dt.date) -> list[CachedVintage]:
        directory = self.root / report_id
        if not directory.exists():
            return []
        found: list[CachedVintage] = []
        for path in sorted(directory.glob("*.csv.gz")):
            meta_path = path.with_name(path.name.removesuffix(".csv.gz") + ".meta.json")
            if not meta_path.exists():
                continue
            meta: dict[str, Any] = json.loads(meta_path.read_text())
            posted = pd.Timestamp(meta["posted_time"])
            if posted.tzinfo is None:
                posted = posted.tz_localize("UTC")
            else:
                posted = posted.tz_convert("UTC")
            posted_day = posted.tz_convert(CPT).date()
            if posted_day < start or posted_day > end:
                continue
            rows = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
            found.append(CachedVintage(report_id, path.name.removesuffix(".csv.gz"), posted, meta, rows))
        return found

    def api_covers(self, report_id: str, start: dt.date, end: dt.date) -> bool:
        """True when a previous API fetch already covered every complete day in range."""
        marker = self._marker(report_id)
        if not marker.exists():
            return False
        covered: list[list[str]] = json.loads(marker.read_text()).get("ranges", [])
        day = start
        while day <= end:
            if not any(a <= day.isoformat() <= b for a, b in covered):
                return False
            day += dt.timedelta(days=1)
        return True

    def mark_api(self, report_id: str, start: dt.date, end: dt.date) -> None:
        if end < start:
            return
        marker = self._marker(report_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        current: list[list[str]] = []
        if marker.exists():
            current = json.loads(marker.read_text()).get("ranges", [])
        current.append([start.isoformat(), end.isoformat()])
        marker.write_text(json.dumps({"ranges": _merge_ranges(current)}, indent=2) + "\n")

    def _marker(self, report_id: str) -> Path:
        return self.root / report_id / "_api_coverage.json"


def _merge_ranges(ranges: list[list[str]]) -> list[list[str]]:
    parsed = sorted((dt.date.fromisoformat(a), dt.date.fromisoformat(b)) for a, b in ranges)
    merged: list[list[str]] = []
    for start, end in parsed:
        if merged and dt.date.fromisoformat(merged[-1][1]) + dt.timedelta(days=1) >= start:
            if end.isoformat() > merged[-1][1]:
                merged[-1][1] = end.isoformat()
        else:
            merged.append([start.isoformat(), end.isoformat()])
    return merged
