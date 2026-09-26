"""Where the harness keeps local data: $HARNESS_DATA_DIR, or data/ at the repo root.

    data/raw/           raw ERCOT downloads (harness.market, harness.base_actual)
    data/market/        the normalized market dataset and Base-actual table
    data/forecast-raw/  raw forecast vintages (harness.forecast)
    data/forecasts/     the point-in-time forecast-input store
    data/runs/          scorecards and data dumps written by `harness run`

All of it is git-ignored.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    env = os.environ.get("HARNESS_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "data"


def runs_dir() -> Path:
    return data_dir() / "runs"
