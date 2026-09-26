"""Raw cache: one file per (report, operating day), in the report's own CSV columns.

Raw files keep ERCOT's text as published (local timestamps, DST flags), so
normalization can be rerun without refetching. A JSON sidecar records the route
that fetched each file.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from harness.market.catalog import Report


class RawCache:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path(self, report: Report, day: dt.date) -> Path:
        return self.root / report.id / f"{day.isoformat()}.csv.gz"

    def meta_path(self, report: Report, day: dt.date) -> Path:
        return self.root / report.id / f"{day.isoformat()}.meta.json"

    def has(self, report: Report, day: dt.date) -> bool:
        return self.path(report, day).exists()

    def read(self, report: Report, day: dt.date) -> pd.DataFrame | None:
        path = self.path(report, day)
        if not path.exists():
            return None
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])

    def meta(self, report: Report, day: dt.date) -> dict[str, Any] | None:
        path = self.meta_path(report, day)
        if not path.exists():
            return None
        meta: dict[str, Any] = json.loads(path.read_text())
        return meta

    def write(self, report: Report, day: dt.date, rows: pd.DataFrame, meta: dict[str, Any]) -> None:
        path = self.path(report, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # mtime=0 keeps the gzip bytes identical across rebuilds.
        rows.loc[:, list(report.columns)].to_csv(
            tmp, index=False, compression={"method": "gzip", "mtime": 0},
        )
        os.replace(tmp, path)
        self.meta_path(report, day).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
