"""What a policy sees each interval: the observation.

The observation has its final six sections now, so policies written against it
keep working as later slices fill them in:

    now         the interval's market row: RT MCPC, load-zone prices, scarcity flags
    history     realized public data posted before the interval (empty for now)
    forecasts   latest forecast vintages posted before the interval (empty for now)
    forecaster  output of the configured price forecaster (empty for now)
    fleet       fleet state (empty for now)
    products    product rules: pilot cap and cap share

It holds only JSON values (missing data is None), so an external policy can
receive it as is.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypedDict

import pandas as pd

from harness.market.catalog import scarce_column
from harness.products import LOAD_ZONES, PRODUCTS
from harness.scenario import Scenario


class Now(TypedDict):
    interval_start_utc: str  # ISO 8601
    interval_start_cpt: str  # ISO 8601 with the CPT offset
    rt_mcpc: dict[str, float | None]  # 5-minute RT MCPC per product, $/MW-h
    lz_price: dict[str, float | None]  # RT settlement point price per load zone, $/MWh
    scarce: dict[str, bool | None]  # scarcity flag per product


class ProductView(TypedDict):
    cap_mw: float
    cap_share: float


class Observation(TypedDict):
    now: Now
    history: dict[str, Any]
    forecasts: dict[str, Any]
    forecaster: dict[str, Any]
    fleet: dict[str, Any]
    products: dict[str, ProductView]


def observations(rows: pd.DataFrame, scenario: Scenario) -> Iterator[Observation]:
    """One observation per row of the market interval table, in order."""
    for start, row in zip(pd.DatetimeIndex(rows.index), rows.to_dict("records")):
        yield Observation(
            now=Now(
                interval_start_utc=start.isoformat(),
                interval_start_cpt=row["interval_start_cpt"].isoformat(),
                rt_mcpc={p: _number(row[f"rt_mcpc_5m_{sfx}"]) for p, sfx in PRODUCTS.items()},
                lz_price={z: _number(row[f"lz_spp_{sfx}"]) for z, sfx in LOAD_ZONES.items()},
                scarce={p: _flag(row[scarce_column(sfx)]) for p, sfx in PRODUCTS.items()},
            ),
            history={},
            forecasts={},
            forecaster={},
            fleet={},
            products={p: ProductView(cap_mw=r.cap_mw, cap_share=r.cap_share)
                      for p, r in scenario.products.items()},
        )


def _number(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _flag(value: Any) -> bool | None:
    return None if pd.isna(value) else bool(value)
