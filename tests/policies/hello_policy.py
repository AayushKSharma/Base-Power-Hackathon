#!/usr/bin/env python3
"""Test policy whose first few starts fail the handshake, then behave.

A state file counts process starts, so a restart does not fail the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--bad-hellos", type=int, default=0)
    parser.add_argument("--crash-hellos", type=int, default=0)
    parser.add_argument("--mw", type=float, default=5.0)
    args = parser.parse_args()

    path = Path(args.state)
    seen = int(path.read_text()) if path.exists() else 0
    path.write_text(str(seen + 1))
    if seen < args.crash_hellos:
        os._exit(1)

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    if seen < args.crash_hellos + args.bad_hellos:
        _send({"name": ""})
        time.sleep(30)
        return 0
    _send({"name": "hello", "version": "1", "wants_per_home": False})
    capability = {"capability_mw": {"ECRS": args.mw, "NONSPIN": args.mw}}
    while True:
        message = _read()
        if message is None:
            return 0
        _send(capability)


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
