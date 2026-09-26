#!/usr/bin/env python3
"""Test policy that sleeps on chosen intervals, then reports a fixed capability."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, required=True)
    parser.add_argument("--mw", type=float, default=4.0)
    parser.add_argument("--at", action="append", default=[], help="interval_start_utc values to sleep on")
    args = parser.parse_args()

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    _send({"name": "slow", "version": "1", "wants_per_home": False})
    capability = {"capability_mw": {"ECRS": args.mw, "NONSPIN": args.mw}}
    slow_at = set(args.at)
    while True:
        message = _read()
        if message is None:
            return 0
        observation = message["observation"]
        if observation["now"]["interval_start_utc"] in slow_at:
            time.sleep(args.sleep)
        _send(capability)


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
