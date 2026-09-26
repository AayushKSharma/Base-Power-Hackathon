"""Normalized dataset on disk: one Parquet file per operating day."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd


class MarketStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, table: str, day: dt.date) -> Path:
        return self.root / table / f"{day.isoformat()}.parquet"

    def write(self, table: str, day: dt.date, df: pd.DataFrame) -> None:
        path = self._path(table, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp)
        os.replace(tmp, path)

    def remove(self, table: str, day: dt.date) -> None:
        self._path(table, day).unlink(missing_ok=True)

    def days(self, table: str) -> list[dt.date]:
        return sorted(dt.date.fromisoformat(p.stem) for p in (self.root / table).glob("*.parquet"))

    def read(self, table: str, days: list[dt.date], columns: list[str] | None = None) -> pd.DataFrame:
        frames = [pd.read_parquet(self._path(table, d), columns=columns) for d in days]
        return pd.concat(frames)
