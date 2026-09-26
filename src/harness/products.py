"""The AS products an ADER fleet can sell, and the load zones it sits in.

Each maps a harness name to its column suffix in the market dataset, taken from
the market catalog, which names every dataset column.
"""

from __future__ import annotations

from harness.market import catalog

# ADERs may sell only ECRS and Non-Spin.
PRODUCTS: dict[str, str] = {
    "ECRS": catalog.PRODUCTS["ECRS"],
    "NONSPIN": catalog.PRODUCTS["NSPIN"],
}

LOAD_ZONES: dict[str, str] = {
    zone: catalog.LOAD_ZONES[f"LZ_{zone}"] for zone in ("HOUSTON", "NORTH", "SOUTH", "WEST")
}
