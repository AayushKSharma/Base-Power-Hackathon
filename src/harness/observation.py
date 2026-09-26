"""What a policy sees each interval: the observation.

The observation has its final six sections now, so policies written against it
keep working as later slices fill them in:

    now         the interval's market row: RT MCPC, load-zone prices, scarcity flags
    history     realized public data posted before the interval (empty for now)
    forecasts   latest forecast vintages posted at or before the interval
                (empty unless the run was given a forecast store)
    forecaster  output of the configured price forecaster (empty for now)
    fleet       fleet state (empty for now)
    products    product rules: pilot cap and cap share

It holds only JSON values (missing data is None), so an external policy can
receive it as is.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any, Protocol, TypedDict

import pandas as pd

from harness.forecast.catalog import DEFAULT_HORIZON
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


class ForecastQuery(Protocol):
    """The forecast-input store, as a run sees it: as_of at a decision time."""

    def as_of(
        self,
        when: pd.Timestamp,
        inputs: list[str] | None = None,
        *,
        horizon: dt.timedelta = DEFAULT_HORIZON,
    ) -> dict[str, Any]: ...


class Observation(TypedDict):
    now: Now
    history: dict[str, Any]
    forecasts: dict[str, Any]
    forecaster: dict[str, Any]
    fleet: dict[str, Any]
    products: dict[str, ProductView]


def observations(
    rows: pd.DataFrame,
    scenario: Scenario,
    *,
    forecasts: ForecastQuery | None = None,
    forecast_horizon: dt.timedelta = DEFAULT_HORIZON,
) -> Iterator[Observation]:
    """One observation per row of the market interval table, in order.

    When `forecasts` is given, each observation's forecasts section is `as_of`
    at that interval's start. Otherwise the section is empty.
    """
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
            forecasts={} if forecasts is None else forecasts.as_of(start, horizon=forecast_horizon),
            forecaster={},
            fleet={},
            products={p: ProductView(cap_mw=r.cap_mw, cap_share=r.cap_share)
                      for p, r in scenario.products.items()},
        )


def _number(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _flag(value: Any) -> bool | None:
    return None if pd.isna(value) else bool(value)
