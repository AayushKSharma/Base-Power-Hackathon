"""JSON-lines protocol between the harness and an external policy or forecaster.

One JSON object per line, UTF-8. The harness speaks first:

    {"type": "hello", "protocol_version": 1, "products": {ECRS: {duration_h, cap_mw, cap_share}, ...}}

The process answers `{name, version, wants_per_home}`, then replies to each

    {"type": "observation", "observation": <the observation object>}

with `{capability_mw: {ECRS, NONSPIN}}`. A capability value is well-formed only
when every product is a finite, non-negative number. Anything else, including
true/false and null, is malformed. A value above the fleet's nominal capability
is allowed: overstatement is what the scorecard measures.

A price forecaster speaks the same hello, then replies to

    {"type": "forecast", "horizon_hours": N, "quantiles": [...], "observation": {...}}

with quantile trajectories for the six price series. `values[i]` is the
trajectory of `quantiles[i]`, one number per hour, starting at the decision time.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

# Load-zone RT settlement prices and RT MCPC for the two ADER products.
PRICE_SERIES = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST", "MCPC_ECRS", "MCPC_NSPIN")

from harness.products import PRODUCTS
from harness.scenario import ProductRules

PROTOCOL_VERSION = 1


def hello_message(products: Mapping[str, ProductRules]) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "products": {name: {"duration_h": rules.duration_h, "cap_mw": rules.cap_mw,
                            "cap_share": rules.cap_share}
                     for name, rules in products.items()},
    }


def observation_message(observation: Mapping[str, Any]) -> dict[str, Any]:
    """The observation object, wrapped so the line has a type."""
    return {"type": "observation", "observation": observation}


def forecast_message(observation: Mapping[str, Any], *, horizon_hours: int,
                     quantiles: Sequence[float]) -> dict[str, Any]:
    """Ask a forecaster for the next `horizon_hours` at these quantiles."""
    return {
        "type": "forecast",
        "horizon_hours": horizon_hours,
        "quantiles": [float(q) for q in quantiles],
        "observation": observation,
    }


def for_policy(observation: Mapping[str, Any], *, wants_per_home: bool) -> dict[str, Any]:
    """Copy of the observation an external policy may see.

    Per-home state is the `homes` list, or `fleet["homes"]` when the fleet
    model stores it there. Both are removed unless the policy asked.
    """
    sent = dict(observation)
    if wants_per_home:
        return sent
    sent.pop("homes", None)
    fleet = sent.get("fleet")
    if isinstance(fleet, dict) and "homes" in fleet:
        fleet = dict(fleet)
        del fleet["homes"]
        sent["fleet"] = fleet
    return sent


def encode(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, allow_nan=False).encode() + b"\n"


def decode(line: bytes) -> Any:
    """The line as JSON, or None when it is not a JSON value."""
    try:
        return json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def parse_hello(message: Any) -> tuple[str, str, bool] | None:
    """(name, version, wants_per_home), or None when the reply is malformed."""
    if not isinstance(message, dict):
        return None
    name, version, wants_per_home = (message.get("name"), message.get("version"),
                                     message.get("wants_per_home"))
    if not isinstance(name, str) or not name or not isinstance(version, str):
        return None
    if not isinstance(wants_per_home, bool):
        return None
    return name, version, wants_per_home


def parse_forecast(message: Any, *, issued_at: str, horizon_hours: int,
                  quantiles: Sequence[float]) -> dict[str, Any] | None:
    """Quantile trajectories, or None when the reply is malformed.

    Each series maps a quantile to one value per hour. `values[i][h]` is the
    forecast of `quantiles[i]` for the hour starting `h` hours after `issued_at`.
    A missing series, a non-finite number, or a trajectory of the wrong length
    is malformed. The first hour may instead be twelve 5-minute steps, in which
    case each trajectory has `12 + horizon_hours - 1` values and the result has
    `step` of `"5m"`.
    """
    if not isinstance(message, dict) or not isinstance(horizon_hours, int) or isinstance(horizon_hours, bool):
        return None
    if horizon_hours < 1 or _stamp(message.get("issued_at")) != _stamp(issued_at):
        return None
    if message.get("horizon_hours") != horizon_hours:
        return None
    wanted = [float(q) for q in quantiles]
    if not wanted or any(not _unit_quantile(q) for q in wanted):
        return None
    raw_series = message.get("series")
    if not isinstance(raw_series, dict) or set(raw_series) != set(PRICE_SERIES):
        return None
    series: dict[str, Any] = {}
    step: str | None = None
    for name in PRICE_SERIES:
        parsed, series_step = _parse_series(raw_series[name], wanted, horizon_hours)
        if parsed is None or series_step is None:
            return None
        if step is None:
            step = series_step
        elif step != series_step:
            return None
        series[name] = parsed
    return {"issued_at": issued_at, "horizon_hours": horizon_hours, "step": step, "series": series}


def _parse_series(raw: Any, quantiles: list[float], horizon_hours: int,
                  ) -> tuple[dict[str, Any], str] | tuple[None, None]:
    if not isinstance(raw, dict) or _as_floats(raw.get("quantiles")) != quantiles:
        return None, None
    values = raw.get("values")
    if not isinstance(values, list) or len(values) != len(quantiles):
        return None, None
    hourly = horizon_hours
    five_min = 12 + horizon_hours - 1
    length = len(values[0]) if isinstance(values[0], list) else None
    if length == hourly:
        step = "1h"
    elif length == five_min:
        step = "5m"
    else:
        return None, None
    cleaned: list[list[float]] = []
    for row in values:
        if not isinstance(row, list) or len(row) != length:
            return None, None
        numbers = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                return None, None
            numbers.append(float(value))
        cleaned.append(numbers)
    return {"quantiles": quantiles, "values": cleaned}, step


def _as_floats(raw: Any) -> list[float] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int | float) or not _unit_quantile(float(value)):
            return None
        out.append(float(value))
    return out


def _unit_quantile(value: float) -> bool:
    return math.isfinite(value) and 0.0 < value < 1.0


def _stamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def parse_capability(message: Any) -> dict[str, float] | None:
    """Reported MW per product, or None when the reply is malformed."""
    if not isinstance(message, dict):
        return None
    raw = message.get("capability_mw")
    if not isinstance(raw, dict):
        return None
    capability = {}
    for product in PRODUCTS:
        value = raw.get(product)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        capability[product] = float(value)
    return capability
