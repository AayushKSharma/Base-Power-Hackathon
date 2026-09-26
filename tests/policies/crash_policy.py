#!/usr/bin/env python3
"""Test policy that exits on one observation, then would behave if restarted."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash-at", required=True, help="interval_start_utc that exits the process")
    parser.add_argument("--mw", type=float, default=6.0)
    args = parser.parse_args()

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    _send({"name": "crash", "version": "1", "wants_per_home": False})
    capability = {"capability_mw": {"ECRS": args.mw, "NONSPIN": args.mw}}
    while True:
        message = _read()
        if message is None:
            return 0
        # Keyed off the observation so a restarted process does not crash again.
        if message["observation"]["now"]["interval_start_utc"] == args.crash_at:
            os._exit(1)
        _send(capability)


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
