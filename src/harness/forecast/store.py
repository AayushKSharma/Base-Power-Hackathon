"""Forecast-input store: one Parquet file per input and posted date (CPT)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd

from harness.forecast.catalog import BY_ID, DEFAULT_HORIZON, INPUTS, ForecastInput
from harness.forecast.parse import empty_frame


class ForecastRow(TypedDict):
    posted_time: str
    valid_time: str
    series: str
    value: float
    in_use: bool | None


class ForecastStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, pd.DataFrame] = {}

    def path(self, input_id: str, day: dt.date) -> Path:
        return self.root / input_id / f"{day.isoformat()}.parquet"

    def write(self, input_id: str, day: dt.date, frame: pd.DataFrame) -> None:
        path = self.path(input_id, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        os.replace(tmp, path)
        self._cache.pop(input_id, None)

    def remove(self, input_id: str, day: dt.date) -> None:
        self.path(input_id, day).unlink(missing_ok=True)
        self._cache.pop(input_id, None)

    def days(self, input_id: str) -> list[dt.date]:
        directory = self.root / input_id
        if not directory.exists():
            return []
        return sorted(dt.date.fromisoformat(path.stem) for path in directory.glob("*.parquet"))

    def load(self, input_id: str) -> pd.DataFrame:
        """Every stored row of one input, sorted by posted time, valid time, series."""
        cached = self._cache.get(input_id)
        if cached is not None:
            return cached
        days = self.days(input_id)
        if not days:
            frame = empty_frame()
        else:
            frames = [pd.read_parquet(self.path(input_id, day)) for day in days]
            frame = pd.concat(frames, ignore_index=True)
            frame = frame.sort_values(["posted_time", "valid_time", "series"], kind="stable")
            frame = frame.reset_index(drop=True)
        self._cache[input_id] = frame
        return frame

    def as_of(
        self,
        when: pd.Timestamp,
        inputs: list[str] | None = None,
        *,
        horizon: dt.timedelta = DEFAULT_HORIZON,
    ) -> dict[str, list[ForecastRow]]:
        """Per input, the latest vintage posted at or before `when`, within `horizon`.

        A row posted after `when` is never returned. Valid times are the half-open
        interval [when, when + horizon).
        """
        if horizon < dt.timedelta(0):
            raise ValueError(f"horizon must be non-negative, got {horizon}")
        moment = pd.Timestamp(when)
        if moment.tzinfo is None:
            raise ValueError("as_of time must be timezone-aware")
        moment = moment.tz_convert("UTC")
        selected = _select(inputs)
        until = moment + horizon
        out: dict[str, list[ForecastRow]] = {}
        for spec in selected:
            frame = self.load(spec.id)
            if frame.empty:
                continue
            known = frame[frame["posted_time"] <= moment]
            if known.empty:
                continue
            latest = known["posted_time"].max()
            rows = known[
                (known["posted_time"] == latest)
                & (known["valid_time"] >= moment)
                & (known["valid_time"] < until)
            ]
            if rows.empty:
                continue
            records: list[dict[str, Any]] = rows.to_dict("records")
            out[spec.id] = [_json_row(record) for record in records]
        return out

    def inputs_on_disk(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.name for path in self.root.iterdir() if path.is_dir() and any(path.glob("*.parquet")))


def _select(inputs: list[str] | None) -> tuple[ForecastInput, ...]:
    if inputs is None:
        return INPUTS
    unknown = [name for name in inputs if name not in BY_ID]
    if unknown:
        known = ", ".join(item.id for item in INPUTS)
        raise ValueError(f"unknown forecast input {unknown[0]!r}; known inputs: {known}")
    return tuple(BY_ID[name] for name in inputs)


def _json_row(record: dict[str, Any]) -> ForecastRow:
    in_use = record["in_use"]
    return {
        "posted_time": pd.Timestamp(record["posted_time"]).isoformat(),
        "valid_time": pd.Timestamp(record["valid_time"]).isoformat(),
        "series": str(record["series"]),
        "value": float(record["value"]),
        "in_use": None if pd.isna(in_use) else bool(in_use),
    }
