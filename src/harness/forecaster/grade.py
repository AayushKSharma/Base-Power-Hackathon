"""Grade quantile price forecasts against realized hourly prices.

Metrics are means over scored points (one series, one lead, one issue time):

- MAE and RMSE use the 0.5 quantile.
- Pinball loss at quantile q is q*(y - yhat) when y >= yhat, else (q - 1)*(y - yhat).
- Coverage is the share of outcomes lying inside the lowest and highest
  predicted quantiles, inclusive.
- A point is a spike when its realized price is among the top `spike_top`
  fraction of realized prices in that cell (at least one point). The forecast
  calls a spike when its median clears that same realized threshold.

Horizon buckets do not overlap. `next_hour` is a lead under 1 hour, `h1_6` is
a lead from 1 hour up to 6 hours, and `h6_24` is a lead from 6 hours through
24. A target hour is scarce when any interval in it is scarce for either product.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from harness.forecast.catalog import DEFAULT_HORIZON
from harness.observation import ForecastQuery
from harness.products import LOAD_ZONES, PRODUCTS
from harness.forecaster.features import feature_history
from harness.policy import PolicyError
from harness.protocol import PRICE_SERIES, parse_forecast
from harness.scorecard import FaultCounts

# Realized column in the interval table for each forecast series.
_REALIZED = {
    "LZ_HOUSTON": "lz_spp_houston",
    "LZ_NORTH": "lz_spp_north",
    "LZ_SOUTH": "lz_spp_south",
    "LZ_WEST": "lz_spp_west",
    "MCPC_ECRS": "rt_mcpc_5m_ecrs",
    "MCPC_NSPIN": "rt_mcpc_5m_nspin",
}
HORIZONS = ("next_hour", "h1_6", "h6_24")
REGIMES = ("calm", "scarce")

MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]


@dataclass(frozen=True)
class MetricCell:
    """Accuracy on one slice of scored points. Rates are None when undefined."""

    n: int
    mae: float | None
    rmse: float | None
    pinball: dict[float, float | None]
    coverage: float | None
    spike_precision: float | None
    spike_recall: float | None


@dataclass(frozen=True)
class ForecasterScore:
    name: str
    look_ahead: bool
    faults: FaultCounts
    overall: MetricCell
    by_horizon: dict[str, MetricCell]
    by_regime: dict[str, MetricCell]
    by_horizon_regime: dict[str, dict[str, MetricCell]]
    by_series: dict[str, MetricCell]


@dataclass(frozen=True)
class GradeReport:
    scores: tuple[ForecasterScore, ...]
    quantiles: tuple[float, ...] = ()
    spike_top: float = 0.1
    start: dt.date | None = None
    end: dt.date | None = None


@dataclass(frozen=True)
class _Point:
    series: str
    lead_h: float
    regime: str
    y: float
    pred: tuple[float, ...]


def grade(
    forecasters: Sequence[Any],
    start: dt.date,
    end: dt.date,
    *,
    market: MarketLoader,
    forecasts: ForecastQuery | None = None,
    horizon_hours: int = 24,
    quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    spike_top: float = 0.1,
    forecast_horizon: dt.timedelta = DEFAULT_HORIZON,
) -> GradeReport:
    """Score each forecaster over operating days [start, end].

    `forecasts`, when given, is read only through `as_of` at the decision time.
    A realized price enters a score only after that hour has passed, and only
    as the target, not as an input.
    """
    if end < start:
        raise ValueError(f"start {start} is after end {end}")
    if horizon_hours < 1:
        raise ValueError(f"horizon_hours must be at least 1, got {horizon_hours}")
    levels = tuple(float(q) for q in quantiles)
    if not any(math.isclose(q, 0.5) for q in levels):
        raise ValueError("quantiles must include 0.5, the median the grader scores")
    if not 0 < spike_top <= 1:
        raise ValueError(f"spike_top must be in (0, 1], got {spike_top}")
    frame = _intervals(market(start, end))
    issued = _issue_times(frame, start, end)
    pairs = []
    try:
        for forecaster in forecasters:
            points, faults = _score_one(
                forecaster, frame, issued, forecasts, horizon_hours, levels, forecast_horizon,
            )
            pairs.append((forecaster, points, faults))
    finally:
        for forecaster in forecasters:
            close = getattr(forecaster, "close", None)
            if callable(close):
                close()
    return GradeReport(
        scores=tuple(_summarize(forecaster, points, faults, levels, spike_top)
                     for forecaster, points, faults in pairs),
        quantiles=levels,
        spike_top=spike_top,
        start=start,
        end=end,
    )


def _score_one(forecaster: Any, frame: pd.DataFrame, issued: list[tuple[pd.Timestamp, dt.date]],
               forecasts: ForecastQuery | None, horizon_hours: int, quantiles: tuple[float, ...],
               forecast_horizon: dt.timedelta) -> tuple[list[_Point], FaultCounts]:
    points: list[_Point] = []
    faults = FaultCounts()
    # An external forecaster records the shared session's faults itself.
    recorded = getattr(forecaster, "faults", None)
    owns_faults = isinstance(recorded, FaultCounts)
    before = recorded if owns_faults else FaultCounts()
    last_good: dict[str, Any] | None = None
    day: dt.date | None = None
    for moment, operating_day in issued:
        if operating_day != day:
            begin = getattr(forecaster, "begin_day", None)
            if callable(begin):
                begin()
            last_good = None
            day = operating_day
        view = decision_view(moment, frame, forecasts, forecast_horizon)
        parsed, last_good, extra = ask(
            forecaster, view, horizon_hours=horizon_hours, quantiles=quantiles,
            owns_faults=owns_faults, last_good=last_good,
        )
        faults = faults + extra
        points.extend(_points(parsed, frame, quantiles))
    if owns_faults:
        return points, forecaster.faults - before
    return points, faults


def ask(forecaster: Any, view: Mapping[str, Any], *, horizon_hours: int, quantiles: Sequence[float],
        owns_faults: bool, last_good: dict[str, Any] | None,
        ) -> tuple[dict[str, Any], dict[str, Any] | None, FaultCounts]:
    """One forecast. In-process failures become the fallback; an external session
    has already counted its own faults and returns a usable reply.
    """
    issued = view["now"]["interval_start_utc"]
    faults = FaultCounts()
    try:
        reply = forecaster.forecast(view, horizon_hours=horizon_hours, quantiles=quantiles)
    except PolicyError:
        raise
    except Exception:
        if not owns_faults:
            faults = FaultCounts(restarts=1, fallbacks=1)
        reply = None
    parsed = None if reply is None else parse_forecast(
        reply, issued_at=issued, horizon_hours=horizon_hours, quantiles=quantiles,
    )
    if parsed is None:
        if not owns_faults and faults == FaultCounts():
            faults = FaultCounts(malformed=1, fallbacks=1)
        levels = tuple(float(q) for q in quantiles)
        remembered = None if owns_faults else last_good
        return _fallback(remembered, issued, horizon_hours, levels), last_good, faults
    if owns_faults:
        return parsed, last_good, faults
    return parsed, parsed, faults


def zero_forecast(issued_at: str, horizon_hours: int, quantiles: Sequence[float]) -> dict[str, Any]:
    """A well-formed trajectory of zeros, used before any good reply."""
    levels = [float(q) for q in quantiles]
    values = [[0.0] * horizon_hours for _ in levels]
    return {
        "issued_at": issued_at,
        "horizon_hours": horizon_hours,
        "step": "1h",
        "series": {name: {"quantiles": levels, "values": [row[:] for row in values]}
                   for name in PRICE_SERIES},
    }


def _fallback(last_good: dict[str, Any] | None, issued_at: str, horizon_hours: int,
              quantiles: tuple[float, ...]) -> dict[str, Any]:
    if last_good is not None and last_good["horizon_hours"] == horizon_hours:
        stamped = dict(last_good)
        stamped["issued_at"] = issued_at
        return stamped
    return zero_forecast(issued_at, horizon_hours, quantiles)


def _points(parsed: Mapping[str, Any], frame: pd.DataFrame, quantiles: tuple[float, ...]) -> list[_Point]:
    issued = pd.Timestamp(parsed["issued_at"]).tz_convert("UTC")
    out = []
    for lead, start, width in _leads(issued, parsed["step"], parsed["horizon_hours"]):
        window = frame[(frame.index >= start) & (frame.index < start + width)]
        if window.empty:
            continue
        regime = _regime(window)
        slot = _step_index(parsed["step"], lead, parsed["horizon_hours"])
        for series in PRICE_SERIES:
            realized = _realized(window, _REALIZED[series], width)
            if realized is None:
                continue
            rows = parsed["series"][series]["values"]
            pred = tuple(float(rows[q][slot]) for q in range(len(quantiles)))
            out.append(_Point(series, lead, regime, realized, pred))
    return out


def _step_index(step: str, lead_h: float, horizon_hours: int) -> int:
    if step == "1h":
        return int(lead_h)
    if lead_h < 1:
        return int(round(lead_h * 60 / 5))
    return 12 + int(lead_h) - 1


def _leads(issued: pd.Timestamp, step: str, horizon_hours: int):
    if step == "1h":
        for hour in range(horizon_hours):
            yield float(hour), issued + pd.Timedelta(hours=hour), pd.Timedelta(hours=1)
        return
    for slot in range(12):
        yield slot * 5 / 60, issued + pd.Timedelta(minutes=5 * slot), pd.Timedelta(minutes=5)
    for hour in range(1, horizon_hours):
        yield float(hour), issued + pd.Timedelta(hours=hour), pd.Timedelta(hours=1)


def realized_price(frame: pd.DataFrame, start: pd.Timestamp, width: pd.Timedelta, series: str) -> float | None:
    """The grader's target: the mean price of `series` on [start, start + width)."""
    window = frame[(frame.index >= start) & (frame.index < start + width)]
    if window.empty:
        return None
    return _realized(window, _REALIZED[series], width)


def _realized(window: pd.DataFrame, column: str, width: pd.Timedelta) -> float | None:
    if column not in window:
        return None
    values = pd.to_numeric(window[column], errors="coerce").dropna()
    if values.empty:
        return None
    if width <= pd.Timedelta(minutes=5):
        return float(values.iloc[0])
    return float(values.mean())


def _regime(window: pd.DataFrame) -> str:
    for column in ("scarce_ecrs", "scarce_nspin"):
        if column not in window:
            continue
        flags = window[column].dropna()
        if flags.astype(bool).any():
            return "scarce"
    return "calm"


def decision_view(moment: pd.Timestamp, frame: pd.DataFrame, forecasts: ForecastQuery | None,
                  horizon: dt.timedelta) -> dict[str, Any]:
    """What a forecaster may see at `moment`.

    Prices and forecast vintages are strictly from before `moment`. The interval
    that starts at `moment` is the first hour being forecast, so its realized
    price is not an input. Forecast inputs come only from `as_of`.
    """
    moment = pd.Timestamp(moment).tz_convert("UTC")
    current = frame.loc[moment]
    prior = frame[frame.index < moment]
    latest = None if prior.empty else prior.iloc[-1]
    return {
        "now": {
            "interval_start_utc": moment.isoformat(),
            "interval_start_cpt": _timestamp(current["interval_start_cpt"]).isoformat(),
            "rt_mcpc": _prices(latest, {product: f"rt_mcpc_5m_{suffix}" for product, suffix in PRODUCTS.items()}),
            "lz_price": _prices(latest, {zone: f"lz_spp_{suffix}" for zone, suffix in LOAD_ZONES.items()}),
            "scarce": _scarce(latest),
        },
        "history": {
            "realized": [_realized_row(ts, row) for ts, row in prior.iterrows()],
            "features": feature_history(moment, prior, forecasts, horizon),
        },
        "forecasts": {} if forecasts is None else forecasts.as_of(moment, horizon=horizon),
        "forecaster": {},
        "fleet": {"regions": []},
        "products": {},
    }


def _prices(row: pd.Series | None, columns: Mapping[str, str]) -> dict[str, float | None]:
    if row is None:
        return {name: None for name in columns}
    return {name: _number(row[column]) if column in row.index else None for name, column in columns.items()}


def _scarce(row: pd.Series | None) -> dict[str, bool | None]:
    if row is None:
        return {product: None for product in PRODUCTS}
    return {product: _flag(row[f"scarce_{suffix}"]) if f"scarce_{suffix}" in row.index else None
            for product, suffix in PRODUCTS.items()}


def _realized_row(moment: Any, row: pd.Series) -> dict[str, Any]:
    return {
        "valid_time": pd.Timestamp(moment).isoformat(),
        "prices": {series: _number(row[column]) if column in row.index else None
                   for series, column in _REALIZED.items()},
        "scarce": _regime(row.to_frame().T) == "scarce",
    }


def _summarize(forecaster: Any, points: list[_Point], faults: FaultCounts,
               quantiles: tuple[float, ...], spike_top: float) -> ForecasterScore:
    def cell(chosen: list[_Point]) -> MetricCell:
        return _cell(chosen, quantiles, spike_top)

    by_horizon = {name: cell([p for p in points if _bucket(p.lead_h) == name]) for name in HORIZONS}
    by_regime = {name: cell([p for p in points if p.regime == name]) for name in REGIMES}
    crossed = {
        horizon: {regime: cell([p for p in points if _bucket(p.lead_h) == horizon and p.regime == regime])
                  for regime in REGIMES}
        for horizon in HORIZONS
    }
    by_series = {name: cell([p for p in points if p.series == name]) for name in PRICE_SERIES}
    return ForecasterScore(
        name=str(forecaster.name),
        look_ahead=bool(getattr(forecaster, "look_ahead", False)),
        faults=faults,
        overall=cell(points),
        by_horizon=by_horizon,
        by_regime=by_regime,
        by_horizon_regime=crossed,
        by_series=by_series,
    )


def _cell(points: Sequence[_Point], quantiles: tuple[float, ...], spike_top: float) -> MetricCell:
    n = len(points)
    pinball: dict[float, float | None] = {q: None for q in quantiles}
    if n == 0:
        return MetricCell(0, None, None, pinball, None, None, None)
    median_at = _median_index(quantiles)
    abs_err = [abs(p.y - p.pred[median_at]) for p in points]
    sq_err = [(p.y - p.pred[median_at]) ** 2 for p in points]
    for i, q in enumerate(quantiles):
        pinball[q] = sum(_pinball(p.y, p.pred[i], q) for p in points) / n
    inside = sum(1 for p in points if min(p.pred) <= p.y <= max(p.pred))
    precision, recall = _spikes(points, median_at, spike_top)
    return MetricCell(n, sum(abs_err) / n, math.sqrt(sum(sq_err) / n), pinball, inside / n, precision, recall)


def _pinball(y: float, yhat: float, quantile: float) -> float:
    diff = y - yhat
    return quantile * diff if diff >= 0 else (quantile - 1) * diff


def _spikes(points: Sequence[_Point], median_at: int, spike_top: float) -> tuple[float | None, float | None]:
    ordered = sorted(p.y for p in points)
    k = max(1, math.ceil(len(points) * spike_top))
    threshold = ordered[-k]
    actual = [p.y >= threshold for p in points]
    called = [p.pred[median_at] >= threshold for p in points]
    true_positive = sum(1 for flag, call in zip(actual, called) if flag and call)
    predicted = sum(called)
    positives = sum(actual)
    precision = true_positive / predicted if predicted else None
    recall = true_positive / positives if positives else None
    return precision, recall


def _median_index(quantiles: Sequence[float]) -> int:
    for i, quantile in enumerate(quantiles):
        if math.isclose(quantile, 0.5):
            return i
    raise ValueError("quantiles must include 0.5")


def _bucket(lead_h: float) -> str:
    if lead_h < 1:
        return "next_hour"
    if lead_h < 6:
        return "h1_6"
    return "h6_24"


def _intervals(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_index()
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        raise ValueError("market intervals must be indexed by a timezone-aware interval_start_utc")
    out = out.copy()
    out.index = index.tz_convert("UTC")
    out.index.name = "interval_start_utc"
    return out


def _issue_times(frame: pd.DataFrame, start: dt.date, end: dt.date) -> list[tuple[pd.Timestamp, dt.date]]:
    found = []
    seen: set[pd.Timestamp] = set()
    for moment in frame.index:
        stamp = pd.Timestamp(moment)
        if stamp.minute or stamp.second or stamp.microsecond or stamp.nanosecond or stamp in seen:
            continue
        row = frame.loc[stamp]
        day = _day(row["operating_day"])
        if start <= day <= end:
            seen.add(stamp)
            found.append((stamp, day))
    return found


def _day(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()


def _timestamp(value: Any) -> pd.Timestamp:
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    if isinstance(value, pd.Timestamp):
        return value
    return pd.Timestamp(str(value))


def _number(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _flag(value: Any) -> bool | None:
    return None if pd.isna(value) else bool(value)
