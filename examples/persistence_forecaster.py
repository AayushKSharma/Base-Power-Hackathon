#!/usr/bin/env python3
"""Persistence price forecaster for the harness JSON-lines protocol.

Copy this process and keep the loop: read one JSON object per line from stdin,
write one per line to stdout, and flush every line. The forecast message is
documented in docs/policy-protocol.md.

The forecast repeats the latest realized price in the observation for every
hour and every quantile. That price is strictly before the decision time.

    python examples/persistence_forecaster.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

SERIES = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST", "MCPC_ECRS", "MCPC_NSPIN")


def main() -> int:
    hello = _read()
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        print("expected a hello message", file=sys.stderr)
        return 2
    _send({"name": "persistence", "version": "1", "wants_per_home": False, "look_ahead": False})
    while True:
        message = _read()
        if message is None:
            return 0
        if not isinstance(message, dict) or message.get("type") != "forecast":
            print("expected a forecast message", file=sys.stderr)
            return 2
        _send(_forecast(message))


def _forecast(message: dict[str, Any]) -> dict[str, Any]:
    observation = message["observation"]
    horizon = int(message["horizon_hours"])
    quantiles = list(message["quantiles"])
    last = _last(observation)
    hours = {name: [last[name]] * horizon for name in SERIES}
    return {
        "issued_at": observation["now"]["interval_start_utc"],
        "horizon_hours": horizon,
        "series": {
            name: {"quantiles": quantiles, "values": [hours[name][:] for _ in quantiles]}
            for name in SERIES
        },
    }


def _last(observation: dict[str, Any]) -> dict[str, float]:
    chosen: dict[str, tuple[str, float]] = {}
    for row in observation.get("history", {}).get("realized", []):
        stamp = str(row["valid_time"])
        for series, value in row["prices"].items():
            if value is None or series not in SERIES:
                continue
            previous = chosen.get(series)
            if previous is None or stamp >= previous[0]:
                chosen[series] = (stamp, float(value))
    return {series: chosen[series][1] if series in chosen else 0.0 for series in SERIES}


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
