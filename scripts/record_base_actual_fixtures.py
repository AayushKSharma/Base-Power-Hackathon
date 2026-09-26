"""Record the raw NP3-965-ER fixture the Base-actual tests build from.

    .venv/bin/python scripts/record_base_actual_fixtures.py

Fetches the ALR rows of the 60-Day SCED Disclosure load-resource table for
2026-07-20 (published 2026-09-18) into tests/fixtures/base_actual_raw. That day
has a known dispatch-down: at 20:20:22 CDT SCED set OB_ALD1's Base Point
24.99 MW below its consumption, and SANSM_ALD1's 6.10 MW below.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path

from harness.base_actual.source import LOAD_RESOURCES, DisclosureRoute
from harness.market.raw import RawCache
from harness.market.sources import MisClient

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = RawCache(ROOT / "tests" / "fixtures" / "base_actual_raw")
DAYS = [dt.date(2026, 7, 20)]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if FIXTURES.root.exists():
        shutil.rmtree(FIXTURES.root)
    for day, (rows, meta) in DisclosureRoute(MisClient()).fetch(LOAD_RESOURCES, DAYS).items():
        FIXTURES.write(LOAD_RESOURCES, day, rows, meta)
    print(f"fixtures written to {FIXTURES.root}")
