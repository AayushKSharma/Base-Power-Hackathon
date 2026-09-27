#!/usr/bin/env python3
"""Test forecaster: one price for every series, with optional faults."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

SERIES = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST", "MCPC_ECRS", "MCPC_NSPIN")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--price", type=float, default=4.0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--at", action="append", default=[], help="decision times that sleep")
    parser.add_argument("--crash-at", default="", help="decision time that exits")
    parser.add_argument("--bad-at", default="", help="decision time that replies with an empty object")
    args = parser.parse_args()

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    _send({"name": "fixed", "version": "1", "wants_per_home": False, "look_ahead": False})
    slow_at = set(args.at)
    while True:
        message = _read()
        if message is None:
            return 0
        if message.get("type") != "forecast":
            print("expected a forecast message", file=sys.stderr)
            return 2
        issued = message["observation"]["now"]["interval_start_utc"]
        if issued == args.crash_at:
            os._exit(1)
        if issued in slow_at:
            time.sleep(args.sleep)
        if issued == args.bad_at:
            _send({})
            continue
        _send(_trajectory(message, args.price))


def _trajectory(message: dict[str, Any], price: float) -> dict[str, Any]:
    horizon = int(message["horizon_hours"])
    quantiles = list(message["quantiles"])
    row = [price] * horizon
    return {
        "issued_at": message["observation"]["now"]["interval_start_utc"],
        "horizon_hours": horizon,
        "series": {name: {"quantiles": quantiles, "values": [row[:] for _ in quantiles]} for name in SERIES},
    }


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
