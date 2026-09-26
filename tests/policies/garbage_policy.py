#!/usr/bin/env python3
"""Test policy that returns a bad capability on chosen observations."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mw", type=float, default=8.0)
    parser.add_argument("--bad", action="append", default=[], metavar="TICK=KIND",
                        help="1-based observation and kind: negative, non_numeric, or missing")
    args = parser.parse_args()
    bad = {}
    for item in args.bad:
        tick, _, kind = item.partition("=")
        bad[int(tick)] = kind

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    _send({"name": "garbage", "version": "1", "wants_per_home": False})
    good = {"capability_mw": {"ECRS": args.mw, "NONSPIN": args.mw}}
    seen = 0
    while True:
        message = _read()
        if message is None:
            return 0
        seen += 1
        _send(_reply(bad.get(seen), good, args.mw))


def _reply(kind: str | None, good: dict[str, Any], mw: float) -> dict[str, Any]:
    if kind == "negative":
        return {"capability_mw": {"ECRS": -1, "NONSPIN": mw}}
    if kind == "non_numeric":
        return {"capability_mw": {"ECRS": "nope", "NONSPIN": mw}}
    if kind == "missing":
        return {"capability_mw": {"NONSPIN": mw}}
    return good


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
