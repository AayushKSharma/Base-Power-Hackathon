"""JSON-lines protocol between the harness and an external policy.

One JSON object per line, UTF-8. The harness speaks first:

    {"type": "hello", "protocol_version": 1, "products": {ECRS: {duration_h, cap_mw, cap_share}, ...}}

The policy answers `{name, version, wants_per_home}`, then replies to each

    {"type": "observation", "observation": <the observation object>}

with `{capability_mw: {ECRS, NONSPIN}}`. A capability value is well-formed only
when every product is a finite, non-negative number. Anything else, including
true/false and null, is malformed. A value above the fleet's nominal capability
is allowed: overstatement is what the scorecard measures.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from harness.products import PRODUCTS
from harness.scenario import ProductRules

PROTOCOL_VERSION = 1


def hello_message(products: Mapping[str, ProductRules]) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "products": {name: {"duration_h": rules.duration_h, "cap_mw": rules.cap_mw,
                            "cap_share": rules.cap_share}
                     for name, rules in products.items()},
    }


def observation_message(observation: Mapping[str, Any]) -> dict[str, Any]:
    """The observation object, wrapped so the line has a type."""
    return {"type": "observation", "observation": observation}


def for_policy(observation: Mapping[str, Any], *, wants_per_home: bool) -> dict[str, Any]:
    """Copy of the observation an external policy may see.

    Per-home state is the `homes` list, or `fleet["homes"]` when the fleet
    model stores it there. Both are removed unless the policy asked.
    """
    sent = dict(observation)
    if wants_per_home:
        return sent
    sent.pop("homes", None)
    fleet = sent.get("fleet")
    if isinstance(fleet, dict) and "homes" in fleet:
        fleet = dict(fleet)
        del fleet["homes"]
        sent["fleet"] = fleet
    return sent


def encode(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, allow_nan=False).encode() + b"\n"


def decode(line: bytes) -> Any:
    """The line as JSON, or None when it is not a JSON value."""
    try:
        return json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def parse_hello(message: Any) -> tuple[str, str, bool] | None:
    """(name, version, wants_per_home), or None when the reply is malformed."""
    if not isinstance(message, dict):
        return None
    name, version, wants_per_home = (message.get("name"), message.get("version"),
                                     message.get("wants_per_home"))
    if not isinstance(name, str) or not name or not isinstance(version, str):
        return None
    if not isinstance(wants_per_home, bool):
        return None
    return name, version, wants_per_home


def parse_capability(message: Any) -> dict[str, float] | None:
    """Reported MW per product, or None when the reply is malformed."""
    if not isinstance(message, dict):
        return None
    raw = message.get("capability_mw")
    if not isinstance(raw, dict):
        return None
    capability = {}
    for product in PRODUCTS:
        value = raw.get(product)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        capability[product] = float(value)
    return capability
