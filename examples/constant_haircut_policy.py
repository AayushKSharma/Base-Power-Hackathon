#!/usr/bin/env python3
"""Constant-haircut policy for the harness JSON-lines protocol.

Copy this process and keep the loop: read one JSON object per line from stdin,
write one per line to stdout, and flush every line. The protocol is documented
in docs/policy-protocol.md.

    python examples/constant_haircut_policy.py --fraction 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fraction", type=float, default=0.9, help="share of observed capability to report (default 0.9)")
    args = parser.parse_args()
    if args.fraction < 0:
        parser.error("fraction must be non-negative")

    hello = _read()
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        print("expected a hello message", file=sys.stderr)
        return 2
    _send({
        "name": f"constant_haircut(fraction={args.fraction:g})",
        "version": "1",
        "wants_per_home": False,
    })

    while True:
        message = _read()
        if message is None:
            return 0
        if not isinstance(message, dict) or message.get("type") != "observation":
            print("expected an observation message", file=sys.stderr)
            return 2
        _send(_capability(message["observation"], args.fraction))


def _capability(observation: dict[str, Any], fraction: float) -> dict[str, Any]:
    """The in-process constant haircut: fraction times capability summed over regions."""
    regions = observation["fleet"]["regions"]
    return {"capability_mw": {
        product: fraction * sum(region["capability_kw"][product] for region in regions) / 1000
        for product in ("ECRS", "NONSPIN")
    }}


def _read() -> Any:
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)


def _send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
