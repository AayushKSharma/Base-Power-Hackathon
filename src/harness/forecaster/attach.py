"""Fill a run's forecaster section: one trajectory per hour, from data before that hour."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd

from harness.forecaster.grade import _intervals, ask, decision_view
from harness.observation import ForecastQuery, Observation
from harness.scorecard import FaultCounts


def hourly_forecasts(forecaster: Any, rows: pd.DataFrame, forecasts: ForecastQuery | None,
                     forecast_horizon: dt.timedelta, *, horizon_hours: int,
                     quantiles: Sequence[float]) -> Callable[[Observation], Mapping[str, Any]]:
    """A callback for `observations`: the hour's forecast, issued from its point-in-time view."""
    frame = _intervals(rows)
    levels = tuple(float(q) for q in quantiles)
    cache: dict[str, dict[str, Any]] = {}
    last_good: dict[str, Any] | None = None
    owns = isinstance(getattr(forecaster, "faults", None), FaultCounts)

    def call(observation: Observation) -> Mapping[str, Any]:
        nonlocal last_good
        moment = pd.Timestamp(observation["now"]["interval_start_utc"]).floor("h")
        key = moment.isoformat()
        if key not in cache:
            if moment not in frame.index:
                cache[key] = {}
            else:
                view = decision_view(pd.Timestamp(moment), frame, forecasts, forecast_horizon)
                cache[key], last_good, _faults = ask(
                    forecaster, view, horizon_hours=horizon_hours, quantiles=levels,
                    owns_faults=owns, last_good=last_good,
                )
        return cache[key]

    return call
