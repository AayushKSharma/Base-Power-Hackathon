#!/usr/bin/env python3
"""Test policy that records every observation it is sent."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--wants-per-home", action="store_true")
    args = parser.parse_args()

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    _send({"name": "recorder", "version": "1", "wants_per_home": args.wants_per_home})
    zeros = {"capability_mw": {"ECRS": 0.0, "NONSPIN": 0.0}}
    with open(args.out, "w") as out:
        while True:
            message = _read()
            if message is None:
                return 0
            out.write(json.dumps(message["observation"]) + "\n")
            out.flush()
            _send(zeros)


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
