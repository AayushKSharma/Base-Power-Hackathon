"""What the market dataset contains: products, load zones, source reports, columns.

This module is the single place that names ERCOT reports and dataset columns, so
the data README, the normalizer and the loader all agree.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# RTC+B went live for Operating Day 2025-12-05. No RT MCPC exists before it.
RTC_B_START = dt.date(2025, 12, 5)

CPT = "America/Chicago"

# ERCOT AS type code -> column suffix.
PRODUCTS: dict[str, str] = {
    "REGUP": "regup",
    "REGDN": "regdn",
    "RRS": "rrs",
    "ECRS": "ecrs",
    "NSPIN": "nspin",
}

# ERCOT settlement point -> column suffix.
LOAD_ZONES: dict[str, str] = {
    "LZ_HOUSTON": "houston",
    "LZ_NORTH": "north",
    "LZ_SOUTH": "south",
    "LZ_WEST": "west",
}

# NP6-328-CD capability column -> column suffix.
CAPABILITIES: dict[str, str] = {
    "CapREGUPTotal": "regup",
    "CapREGDNTotal": "regdn",
    "CapRRSTotal": "rrs",
    "CapECRSTotal": "ecrs",
    "CapNSPINTotal": "nspin",
    "CapREGUP_RRSTotal": "regup_rrs",
    "CapREGUP_RRS_ECRSTotal": "regup_rrs_ecrs",
    "CapREGUP_RRS_ECRS_NSPINTotal": "regup_rrs_ecrs_nspin",
}


@dataclass(frozen=True)
class Report:
    """An ERCOT report the dataset is built from.

    `id` is the live (CD) report. Raw day files always use its CSV column names,
    whichever route fetched them.
    """

    id: str
    title: str
    columns: tuple[str, ...]
    # Columns that identify one source row; duplicates across documents are dropped.
    keys: tuple[str, ...]
    # Column holding the operating day: a SCED timestamp or a delivery date.
    day_column: str
    # MIS reportTypeId of the live report; MIS keeps a few days to a month.
    live_type_id: int
    # True when one document carries whole operating days (DAM prices, ASDCs),
    # False when each document carries one SCED run or settlement interval.
    daily_documents: bool = False
    # Historical (ER) report with yearly files on MIS, updated weekly.
    archive_id: str | None = None
    archive_type_id: int | None = None


SCED_MCPC = Report(
    id="NP6-332-CD",
    title="Real-Time Clearing Prices for Capacity by SCED Interval",
    columns=("SCEDTimestamp", "RepeatedHourFlag", "ASType", "CappedMCPC", "UncappedMCPC"),
    keys=("SCEDTimestamp", "RepeatedHourFlag", "ASType"),
    day_column="SCEDTimestamp",
    live_type_id=24891,
    archive_id="NP6-795-ER",
    archive_type_id=25569,
)
MCPC_15MIN = Report(
    id="NP6-331-CD",
    title="Real-Time Clearing Prices for Capacity by 15-Minute Settlement Interval",
    columns=(
        "DeliveryDate", "DeliveryHour", "DeliveryInterval", "RepeatedHourFlag", "ASType", "MCPC",
    ),
    keys=("DeliveryDate", "DeliveryHour", "DeliveryInterval", "RepeatedHourFlag", "ASType"),
    day_column="DeliveryDate",
    live_type_id=24898,
    archive_id="NP6-796-ER",
    archive_type_id=25570,
)
DAM_MCPC = Report(
    id="NP4-188-CD",
    title="DAM Clearing Prices for Capacity",
    columns=("DeliveryDate", "HourEnding", "AncillaryType", "MCPC", "DSTFlag"),
    keys=("DeliveryDate", "HourEnding", "DSTFlag", "AncillaryType"),
    day_column="DeliveryDate",
    live_type_id=12329,
    daily_documents=True,
    archive_id="NP4-181-ER",
    archive_type_id=13091,
)
AS_CAPABILITY = Report(
    id="NP6-328-CD",
    title="Total Capability of Resources Available to Provide Ancillary Service",
    columns=("SCEDTimestamp", "RepeatedHourFlag", *CAPABILITIES),
    keys=("SCEDTimestamp", "RepeatedHourFlag"),
    day_column="SCEDTimestamp",
    live_type_id=24887,
    archive_id="NP6-794-ER",
    archive_type_id=25568,
)
SPP = Report(
    id="NP6-905-CD",
    title="Settlement Point Prices at Resource Nodes, Hubs and Load Zones",
    columns=(
        "DeliveryDate", "DeliveryHour", "DeliveryInterval", "SettlementPointName",
        "SettlementPointType", "SettlementPointPrice", "DSTFlag",
    ),
    keys=(
        "DeliveryDate", "DeliveryHour", "DeliveryInterval", "DSTFlag", "SettlementPointName",
        "SettlementPointType",
    ),
    day_column="DeliveryDate",
    live_type_id=12301,
    archive_id="NP6-785-ER",
    archive_type_id=13061,
)
ASDC = Report(
    id="NP4-212-CD",
    title="DAM and SCED Ancillary Service Demand Curves",
    columns=(
        "DeliveryDate", "HourEnding", "ASType", "DemandCurvePoint", "Quantity", "Price",
        "RepeatedHourFlag",
    ),
    keys=("DeliveryDate", "HourEnding", "RepeatedHourFlag", "ASType", "DemandCurvePoint"),
    day_column="DeliveryDate",
    live_type_id=24893,
    daily_documents=True,
)

REPORTS: tuple[Report, ...] = (SCED_MCPC, MCPC_15MIN, DAM_MCPC, AS_CAPABILITY, SPP, ASDC)


@dataclass(frozen=True)
class Field:
    """One value column of the interval table."""

    name: str
    report: Report
    unit: str
    description: str


def _fields() -> tuple[Field, ...]:
    fields: list[Field] = []
    for code, sfx in PRODUCTS.items():
        fields.append(Field(
            f"rt_mcpc_5m_{sfx}", SCED_MCPC, "$/MW-h",
            f"{code} real-time MCPC from the SCED run(s) starting in the interval",
        ))
    for code, sfx in PRODUCTS.items():
        fields.append(Field(
            f"rt_mcpc_15m_{sfx}", MCPC_15MIN, "$/MW-h",
            f"{code} 15-minute settlement RT MCPC, repeated on its three 5-minute rows",
        ))
    for code, sfx in PRODUCTS.items():
        fields.append(Field(
            f"dam_mcpc_{sfx}", DAM_MCPC, "$/MW-h",
            f"{code} day-ahead MCPC, repeated on the hour's twelve 5-minute rows",
        ))
    for point, sfx in LOAD_ZONES.items():
        fields.append(Field(
            f"lz_spp_{sfx}", SPP, "$/MWh",
            f"{point} real-time settlement point price, repeated on its three 5-minute rows",
        ))
    for col, sfx in CAPABILITIES.items():
        fields.append(Field(
            f"as_cap_{sfx}", AS_CAPABILITY, "MW",
            f"System capability available for {col.removeprefix('Cap').removesuffix('Total')}"
            " from the SCED run(s) starting in the interval",
        ))
    return tuple(fields)


FIELDS: tuple[Field, ...] = _fields()
VALUE_COLUMNS: tuple[str, ...] = tuple(f.name for f in FIELDS)


def quality_column(field: str) -> str:
    return f"q_{field}"


def scarce_column(product_suffix: str) -> str:
    return f"scarce_{product_suffix}"


# Per-field quality flag values.
Q_OK = "ok"  # value came from the source for this interval
Q_GAP = "gap"  # the source file exists for the day but has no value for this interval
Q_NO_SOURCE = "no_source"  # no source file for the day
QUALITY_VALUES = (Q_OK, Q_GAP, Q_NO_SOURCE)
