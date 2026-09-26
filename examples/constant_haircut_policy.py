#!/usr/bin/env python3
"""Constant-haircut policy for the harness JSON-lines protocol.

Copy this process and keep the loop: read one JSON object per line from stdin,
write one per line to stdout, and flush every line. The protocol is documented
in docs/policy-protocol.md.

    python examples/constant_haircut_policy.py --fraction 0.9 --nominal-mw 81
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fraction", type=float, default=0.9, help="share of nominal MW to report (default 0.9)")
    parser.add_argument("--nominal-mw", type=float, required=True, help="fleet nominal MW per product")
    args = parser.parse_args()
    if args.fraction < 0 or args.nominal_mw < 0:
        parser.error("fraction and nominal MW must be non-negative")

    hello = _read()
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        print("expected a hello message", file=sys.stderr)
        return 2
    _send({
        "name": f"constant_haircut(fraction={args.fraction:g})",
        "version": "1",
        "wants_per_home": False,
    })

    reported = args.fraction * args.nominal_mw
    capability = {"capability_mw": {"ECRS": reported, "NONSPIN": reported}}
    while True:
        message = _read()
        if message is None:
            return 0
        if not isinstance(message, dict) or message.get("type") != "observation":
            print("expected an observation message", file=sys.stderr)
            return 2
        _send(capability)


def _read() -> Any:
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
