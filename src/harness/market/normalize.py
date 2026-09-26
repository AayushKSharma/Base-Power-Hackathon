"""Align raw ERCOT report rows onto one 5-minute interval grid per operating day.

Rows are keyed internally in UTC. Local timestamps in the raw files are placed
using ERCOT's own repeated-hour/DST flags, so the spring-forward day has 276
intervals and the fall-back day 300.

Values are never forward-filled. A field with no source value for an interval
is NaN, and its `q_<field>` flag says why (see catalog.QUALITY_VALUES).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from harness.market.catalog import (
    AS_CAPABILITY,
    ASDC,
    CAPABILITIES,
    CPT,
    DAM_MCPC,
    FIELDS,
    LOAD_ZONES,
    MCPC_15MIN,
    PRODUCTS,
    Q_GAP,
    Q_NO_SOURCE,
    Q_OK,
    QUALITY_VALUES,
    SCED_MCPC,
    SPP,
    quality_column,
)
from harness.market.raw import RawCache

FIVE_MIN = pd.Timedelta(minutes=5)


def day_grid(day: dt.date) -> pd.DatetimeIndex:
    """Start times (UTC) of the 5-minute intervals of one CPT operating day."""
    start = pd.Timestamp(day).tz_localize(CPT)
    end = pd.Timestamp(day + dt.timedelta(days=1)).tz_localize(CPT)
    grid = pd.date_range(start, end, freq="5min", inclusive="left").tz_convert("UTC")
    return grid.rename("interval_start_utc")


def _time_columns(grid: pd.DatetimeIndex, day: dt.date) -> pd.DataFrame:
    local = grid.tz_convert(CPT)
    wall = local.tz_localize(None)
    offset = wall - grid.tz_localize(None)
    return pd.DataFrame(
        {
            "interval_start_cpt": local,
            "operating_day": pd.Timestamp(day),
            # Central Daylight Time (UTC-5) rather than Central Standard Time (UTC-6).
            "cpt_dst": offset == pd.Timedelta(hours=-5),
            # ERCOT's RepeatedHourFlag: the second pass through 01:00-02:00 on fall-back.
            "repeated_hour": wall.duplicated(keep="first"),
        },
        index=grid,
    )


def _to_utc(wall: pd.Series, repeated_flag: pd.Series) -> pd.Series:
    """Local wall-clock times to UTC. ERCOT flags the second pass through the
    repeated fall-back hour with 'Y'; every other time is unambiguous."""
    ambiguous = (repeated_flag.fillna("N").str.upper() != "Y").to_numpy()
    return wall.dt.tz_localize(CPT, ambiguous=ambiguous, nonexistent="NaT").dt.tz_convert("UTC")


def _numbers(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce")


def _sced_runs_to_grid(runs: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Per-SCED-run values -> per-interval values.

    A SCED run's value holds from its timestamp until the next run. An interval
    takes the time-weighted mean of the runs that started inside it, which is
    that run's value when (as usual) exactly one did. An interval in which no
    run started is left empty.
    """
    runs = runs[runs.index.notna()].sort_index()
    runs = runs[~runs.index.duplicated(keep="last")]
    if runs.empty:
        return pd.DataFrame(index=grid, columns=runs.columns, dtype=float)
    starts = pd.DatetimeIndex(runs.index)
    bins = starts.floor("5min")
    next_start = starts[1:].append(pd.DatetimeIndex([bins[-1] + FIVE_MIN]))
    until = pd.DatetimeIndex(np.minimum(next_start, bins + FIVE_MIN))
    weight = pd.Series((until - starts).total_seconds(), index=starts)
    num = runs.mul(weight, axis=0).groupby(bins).sum(min_count=1)
    den = runs.notna().mul(weight, axis=0).groupby(bins).sum()
    return (num / den.replace(0.0, np.nan)).reindex(grid)


def _sced_runs(rows: pd.DataFrame) -> pd.Series:
    wall = pd.to_datetime(rows["SCEDTimestamp"], format="%m/%d/%Y %H:%M:%S")
    return _to_utc(wall, rows["RepeatedHourFlag"])


def _sced_mcpc(rows: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    rows = rows.assign(run=_sced_runs(rows), value=_numbers(rows["CappedMCPC"]))
    runs = rows.pivot_table(index="run", columns="ASType", values="value", aggfunc="last")
    return _named(_sced_runs_to_grid(runs, grid), "rt_mcpc_5m", PRODUCTS)


def _hour_starts(rows: pd.DataFrame, hour_ending: pd.Series, flag: str) -> pd.Series:
    """UTC start of an ERCOT hour-ending (1-24) delivery hour."""
    date = pd.to_datetime(rows["DeliveryDate"], format="%m/%d/%Y")
    wall = date + pd.to_timedelta(hour_ending - 1, unit="h")
    return _to_utc(wall, rows[flag])


def _spread(values: pd.DataFrame, grid: pd.DatetimeIndex, length: str) -> pd.DataFrame:
    """Repeat each value (indexed by its interval's UTC start) on the 5-minute
    rows it covers. CPT offsets are whole hours, so UTC 15-minute and hour
    boundaries are also CPT ones."""
    values = values[values.index.notna()]
    values = values[~values.index.duplicated(keep="last")]
    return values.reindex(grid.floor(length)).set_axis(grid)


def _quarter_hour_starts(rows: pd.DataFrame, flag: str) -> pd.Series:
    """UTC start of an ERCOT (DeliveryHour, DeliveryInterval 1-4) settlement interval."""
    starts = _hour_starts(rows, _numbers(rows["DeliveryHour"]), flag)
    return starts + pd.to_timedelta((_numbers(rows["DeliveryInterval"]) - 1) * 15, unit="min")


def _named(out: pd.DataFrame, prefix: str, names: dict[str, str]) -> pd.DataFrame:
    """Rename source columns (ERCOT codes) to `<prefix>_<suffix>`; absent ones are NaN."""
    return pd.DataFrame(
        {f"{prefix}_{sfx}": out[code] if code in out else np.nan for code, sfx in names.items()},
        index=out.index,
    )


def _mcpc_15min(rows: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    rows = rows.assign(start=_quarter_hour_starts(rows, "RepeatedHourFlag"), value=_numbers(rows["MCPC"]))
    wide = rows.pivot_table(index="start", columns="ASType", values="value", aggfunc="last")
    return _named(_spread(wide, grid, "15min"), "rt_mcpc_15m", PRODUCTS)


def _dam_mcpc(rows: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    hour = _numbers(rows["HourEnding"].str.slice(0, 2))
    rows = rows.assign(start=_hour_starts(rows, hour, "DSTFlag"), value=_numbers(rows["MCPC"]))
    wide = rows.pivot_table(index="start", columns="AncillaryType", values="value", aggfunc="last")
    return _named(_spread(wide, grid, "h"), "dam_mcpc", PRODUCTS)


def _load_zone_spp(rows: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    # "LZ" is the load zone's settlement point price; "LZEW" is its energy-weighted variant.
    rows = rows[(rows["SettlementPointType"] == "LZ") & rows["SettlementPointName"].isin(LOAD_ZONES)]
    rows = rows.assign(start=_quarter_hour_starts(rows, "DSTFlag"),
                       value=_numbers(rows["SettlementPointPrice"]))
    wide = rows.pivot_table(index="start", columns="SettlementPointName", values="value", aggfunc="last")
    return _named(_spread(wide, grid, "15min"), "lz_spp", LOAD_ZONES)


def _as_capability(rows: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    runs = pd.DataFrame({col: _numbers(rows[col]) for col in CAPABILITIES})
    runs.index = pd.DatetimeIndex(_sced_runs(rows))
    return _named(_sced_runs_to_grid(runs, grid), "as_cap", CAPABILITIES)


# Each interval-table report and the function that puts its raw rows on the grid.
SOURCES = (
    (SCED_MCPC, _sced_mcpc),
    (AS_CAPABILITY, _as_capability),
    (MCPC_15MIN, _mcpc_15min),
    (DAM_MCPC, _dam_mcpc),
    (SPP, _load_zone_spp),
)


def normalize_day(raw: RawCache, day: dt.date) -> pd.DataFrame:
    """The day's interval table: time columns, one column per field, and a
    `q_<field>` quality flag per field."""
    grid = day_grid(day)
    out = _time_columns(grid, day)
    present: set[str] = set()
    for report, convert in SOURCES:
        rows = raw.read(report, day)
        if rows is not None:
            present.add(report.id)
            out = out.join(convert(rows, grid))
    for field in FIELDS:
        values = out[field.name].astype(float) if field.name in out else pd.Series(np.nan, index=grid)
        out[field.name] = values
        if field.report.id in present:
            flag = np.where(values.notna(), Q_OK, Q_GAP)
        else:
            flag = np.full(len(out), Q_NO_SOURCE)
        out[quality_column(field.name)] = pd.Categorical(flag, categories=QUALITY_VALUES)
    return out


ASDC_COLUMNS = ["interval_start_utc", "interval_start_cpt", "product", "point", "quantity_mw", "price"]


def normalize_asdc(raw: RawCache, day: dt.date) -> pd.DataFrame | None:
    """The day's ancillary-service demand curves: one row per (hour, product, curve point)."""
    rows = raw.read(ASDC, day)
    if rows is None:
        return None
    rows = rows[rows["ASType"].isin(PRODUCTS)]
    hour = _numbers(rows["HourEnding"].str.slice(0, 2))
    starts = _hour_starts(rows, hour, "RepeatedHourFlag")
    out = pd.DataFrame({
        "interval_start_utc": starts,
        "interval_start_cpt": starts.dt.tz_convert(CPT),
        "product": rows["ASType"].map(PRODUCTS),
        "point": _numbers(rows["DemandCurvePoint"]).astype("int64"),
        "quantity_mw": _numbers(rows["Quantity"]).astype(float),
        "price": _numbers(rows["Price"]).astype(float),
    })
    out = out.dropna(subset=["interval_start_utc"])
    return out.sort_values(["interval_start_utc", "product", "point"]).reset_index(drop=True)
