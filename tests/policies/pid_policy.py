#!/usr/bin/env python3
"""External policy that records its process id on each hello.

``--crash-once PATH`` makes the first observation of a fresh state file exit,
so the next process started by the harness does not crash on the same file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, help="file that receives one pid per hello")
    parser.add_argument("--mw", type=float, default=4.0)
    parser.add_argument("--crash-once", type=Path, help="crash the first observation, once")
    args = parser.parse_args()

    hello = json.loads(sys.stdin.readline())
    if hello.get("type") != "hello":
        return 2
    with Path(args.log).open("a") as log:
        log.write(f"{os.getpid()}\n")
    _send({"name": "pid_policy", "version": "1", "wants_per_home": False})

    crashed = False
    capability = {"capability_mw": {"ECRS": args.mw, "NONSPIN": args.mw}}
    while True:
        message = _read()
        if message is None:
            return 0
        if args.crash_once is not None and not crashed and not args.crash_once.exists():
            args.crash_once.write_text("crashed")
            os._exit(1)
        crashed = True
        _send(capability)


def _read() -> Any:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
