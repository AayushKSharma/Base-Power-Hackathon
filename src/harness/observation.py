"""What a policy sees each interval: the observation.

The observation has its final six sections now, so policies written against it
keep working as later slices fill them in:

    now         the interval's market row: RT MCPC, load-zone prices, scarcity flags
    history     realized public data posted before the interval (empty for now)
    forecasts   latest forecast vintages posted before the interval (empty for now)
    forecaster  output of the configured price forecaster (empty for now)
    fleet       observed fleet state per region (see harness.fleet)
    products    product rules: duration, pilot cap and cap share

It holds only JSON values (missing data is None), so an external policy can
receive it as is.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypedDict

import pandas as pd

from harness.fleet import FleetCase
from harness.market.catalog import scarce_column
from harness.products import LOAD_ZONES, PRODUCTS
from harness.scenario import Scenario


class Now(TypedDict):
    interval_start_utc: str  # ISO 8601
    interval_start_cpt: str  # ISO 8601 with the CPT offset
    rt_mcpc: dict[str, float | None]  # 5-minute RT MCPC per product, $/MW-h
    lz_price: dict[str, float | None]  # RT settlement point price per load zone, $/MWh
    scarce: dict[str, bool | None]  # scarcity flag per product


class RegionView(TypedDict):
    region_id: int
    homes: int  # homes in the region
    homes_online: int  # reporting fresh telemetry and not in backup mode
    homes_stale: int  # telemetry older than the stale threshold
    homes_backup: int  # in backup mode (grid outage); they never export
    energy_above_floor_kwh: float  # summed over online homes
    inverter_kw: float  # summed over online homes
    # Per product: min(inverter kW, energy above floor / duration), summed over online homes.
    capability_kw: dict[str, float]


class FleetView(TypedDict):
    regions: list[RegionView]


class ProductView(TypedDict):
    duration_h: float
    cap_mw: float
    cap_share: float


class Observation(TypedDict):
    now: Now
    history: dict[str, Any]
    forecasts: dict[str, Any]
    forecaster: dict[str, Any]
    fleet: FleetView
    products: dict[str, ProductView]


def observed_capability_mw(observation: Observation, product: str) -> float:
    """MW the online homes could sustain for the product's duration, if none of
    them dropped out during it."""
    return sum(r["capability_kw"][product] for r in observation["fleet"]["regions"]) / 1000


def observations(rows: pd.DataFrame, scenario: Scenario, case: FleetCase) -> Iterator[Observation]:
    """One observation per row of the market interval table, in order, seeing
    the fleet as the fleet `case` has it at each interval's start."""
    for i, (start, row) in enumerate(zip(pd.DatetimeIndex(rows.index), rows.to_dict("records"))):
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
            fleet=FleetView(regions=[
                RegionView(
                    region_id=r,
                    homes=int(case.homes[i, r]),
                    homes_online=int(case.homes_online[i, r]),
                    homes_stale=int(case.homes_stale[i, r]),
                    homes_backup=int(case.homes_backup[i, r]),
                    energy_above_floor_kwh=float(case.energy_above_floor_kwh[i, r]),
                    inverter_kw=float(case.inverter_kw[i, r]),
                    capability_kw={p: float(kw[i, r]) for p, kw in case.capability_kw.items()},
                )
                for r in range(case.homes.shape[1])
            ]),
            products={p: ProductView(duration_h=r.duration_h, cap_mw=r.cap_mw, cap_share=r.cap_share)
                      for p, r in scenario.products.items()},
        )


def _number(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _flag(value: Any) -> bool | None:
    return None if pd.isna(value) else bool(value)
