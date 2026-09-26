"""Turn one posted ERCOT forecast file into rows keyed by (posted time, valid time).

Each file is one vintage. Rows from a later posting are never folded into an
earlier one; the store keeps both, and `as_of` chooses at read time.
"""

from __future__ import annotations

import re

import pandas as pd

from harness.forecast.catalog import ForecastInput
from harness.market.catalog import CPT

STORED_COLUMNS = ("posted_time", "valid_time", "series", "value", "in_use")

_CANON = re.compile(r"[^a-z0-9]")

# Forecast columns only. Actual generation and COP HSL from the same files are
# realized or scheduled output, not the forecast inputs this store is for.
_WIND_REGIONS = {
    "systemwide": "system",
    "lzsouthhouston": "lz_south_houston",
    "loadzonesouthhouston": "lz_south_houston",
    "lzwest": "lz_west",
    "loadzonewest": "lz_west",
    "lznorth": "lz_north",
    "loadzonenorth": "lz_north",
    "panhandle": "panhandle",
    "coastal": "coastal",
    "south": "south",
    "west": "west",
    "north": "north",
}
_SOLAR_REGIONS = {
    "systemwide": "system",
    "centerwest": "center_west",
    "northwest": "north_west",
    "farwest": "far_west",
    "fareast": "far_east",
    "southeast": "south_east",
    "centereast": "center_east",
}
_LOAD_ZONES = {
    "coast": "coast",
    "east": "east",
    "farwest": "far_west",
    "north": "north",
    "northcentral": "north_central",
    "southcentral": "south_central",
    "southern": "southern",
    "west": "west",
    "systemtotal": "system",
}
_OUTAGE_COLUMNS = {
    "totalresourcemwzonesouth": "total_resource|south",
    "totalresourcemwzonenorth": "total_resource|north",
    "totalresourcemwzonewest": "total_resource|west",
    "totalresourcemwzonehouston": "total_resource|houston",
    "totalirrmwzonesouth": "total_irr|south",
    "totalirrmwzonenorth": "total_irr|north",
    "totalirrmwzonewest": "total_irr|west",
    "totalirrmwzonehouston": "total_irr|houston",
    "totalnewequipresourcemwzonesouth": "total_new_equip|south",
    "totalnewequipresourcemwzonenorth": "total_new_equip|north",
    "totalnewequipresourcemwzonewest": "total_new_equip|west",
    "totalnewequipresourcemwzonehouston": "total_new_equip|houston",
}
_RTD_PRODUCTS = {
    "regup": "REGUP",
    "regdn": "REGDN",
    "rrs": "RRS",
    "ecrs": "ECRS",
    "nspin": "NSPIN",
}
# Settlement-point types that are load zones or hubs. Resource nodes are left out:
# one RTD file is tens of thousands of nodes, and the harness prices zones and hubs.
_RTD_POINT_TYPES = {"LZ", "HU", "SH", "AH"}


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "posted_time": pd.Series(dtype="datetime64[ns, UTC]"),
        "valid_time": pd.Series(dtype="datetime64[ns, UTC]"),
        "series": pd.Series(dtype="string"),
        "value": pd.Series(dtype="float64"),
        "in_use": pd.Series(dtype="boolean"),
    })


def parse_vintage(spec: ForecastInput, rows: pd.DataFrame, posted_time: pd.Timestamp) -> pd.DataFrame:
    """Long rows for one vintage. `posted_time` is the publication time, in UTC."""
    posted = pd.Timestamp(posted_time)
    if posted.tzinfo is None:
        posted = posted.tz_localize("UTC")
    else:
        posted = posted.tz_convert("UTC")
    if rows.empty:
        return empty_frame()
    cols = {_canon(c): c for c in rows.columns}
    parsed = _PARSERS[spec.kind](rows, cols, posted)
    return _finish(parsed)


def _canon(name: str) -> str:
    return _CANON.sub("", name.lower())


def _col(cols: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in cols:
            return cols[name]
    return None


def _require(cols: dict[str, str], *names: str) -> str:
    found = _col(cols, *names)
    if found is None:
        raise ValueError(f"missing column {names[0]!r}; have {sorted(cols)}")
    return found


def _hour_ending(values: pd.Series) -> pd.Series:
    def one(value: object) -> float:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "nat"}:
            return float("nan")
        head = text.split(":", 1)[0]
        try:
            hour = int(float(head))
        except ValueError:
            return float("nan")
        return float(hour) if 1 <= hour <= 24 else float("nan")

    return values.map(one)


def _to_utc(wall: pd.Series, repeated_flag: pd.Series | None) -> pd.Series:
    """Local wall-clock times to UTC. A missing flag treats the repeated fall-back
    hour as daylight time, which is the assumption ERCOT's outage file forces by
    omitting the flag."""
    if repeated_flag is None:
        ambiguous: bool | pd.Series = True
    else:
        ambiguous = repeated_flag.fillna("N").astype(str).str.strip().str.upper() != "Y"
    return wall.dt.tz_localize(CPT, ambiguous=ambiguous, nonexistent="NaT").dt.tz_convert("UTC")


def _delivery_starts(rows: pd.DataFrame, cols: dict[str, str], *, date_names: tuple[str, ...]) -> pd.Series:
    date_col = _require(cols, *date_names)
    hour_col = _require(cols, "hourending")
    flag_col = _col(cols, "dstflag", "dst")
    day = pd.to_datetime(rows[date_col], format="mixed", errors="coerce")
    hours = _hour_ending(rows[hour_col])
    wall = day + pd.to_timedelta(hours - 1, unit="h")
    flags = rows[flag_col] if flag_col else None
    return _to_utc(wall, flags)


def _in_use(rows: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    column = _col(cols, "inuseflag")
    if column is None:
        return pd.Series(pd.NA, index=rows.index, dtype="boolean")
    text = rows[column].astype(str).str.strip().str.lower()
    return text.isin(["y", "true", "1"]).astype("boolean")


def _finish(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return empty_frame()
    out = frame.dropna(subset=["valid_time", "value"]).copy()
    out["posted_time"] = pd.to_datetime(out["posted_time"], utc=True)
    out["valid_time"] = pd.to_datetime(out["valid_time"], utc=True)
    out["series"] = out["series"].astype("string")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["value"])
    out["in_use"] = out["in_use"].astype("boolean")
    out = out.loc[:, list(STORED_COLUMNS)]
    out = out.drop_duplicates(["posted_time", "valid_time", "series"], keep="last")
    return out.sort_values(["posted_time", "valid_time", "series"], kind="stable").reset_index(drop=True)


def _melt(rows: pd.DataFrame, value_of: dict[str, str], valid: pd.Series, posted: pd.Timestamp,
          in_use: pd.Series, series_prefix: pd.Series | None = None) -> pd.DataFrame:
    present = {}
    for column in rows.columns:
        series = value_of.get(_canon(column))
        if series is not None:
            present[series] = rows[column]
    if not present:
        return empty_frame()
    wide = pd.DataFrame(present)
    wide["valid_time"] = valid.to_numpy()
    wide["in_use"] = in_use.to_numpy()
    if series_prefix is not None:
        wide["model"] = series_prefix.to_numpy()
    id_vars = ["valid_time", "in_use"] + (["model"] if series_prefix is not None else [])
    long = wide.melt(id_vars=id_vars, var_name="series", value_name="value")
    long["posted_time"] = posted
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    if series_prefix is not None:
        long["series"] = long["model"].astype(str).str.strip() + "|" + long["series"].astype(str)
        long = long.drop(columns=["model"])
    return long


def _parse_load(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    model_col = _require(cols, "model")
    valid = _delivery_starts(rows, cols, date_names=("deliverydate",))
    return _melt(rows, _LOAD_ZONES, valid, posted, _in_use(rows, cols), rows[model_col])


def _prefixed(prefixes: tuple[str, ...], regions: dict[str, str]) -> dict[str, str]:
    out = {}
    for prefix in prefixes:
        for region, label in regions.items():
            out[prefix + region] = f"{prefix}|{label}"
    return out


def _parse_wind(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    valid = _delivery_starts(rows, cols, date_names=("deliverydate",))
    return _melt(rows, _prefixed(("stwpf", "wgrpp"), _WIND_REGIONS), valid, posted, _in_use(rows, cols))


def _parse_solar(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    valid = _delivery_starts(rows, cols, date_names=("deliverydate",))
    return _melt(rows, _prefixed(("stppf", "pvgrpp"), _SOLAR_REGIONS), valid, posted, _in_use(rows, cols))


def _parse_wind_model(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    valid = _delivery_starts(rows, cols, date_names=("deliverydate",))
    model = rows[_require(cols, "model")].astype(str).str.strip()
    region = rows[_require(cols, "region")].astype(str).str.strip()
    value = pd.to_numeric(rows[_require(cols, "value")], errors="coerce")
    return pd.DataFrame({
        "posted_time": posted,
        "valid_time": valid.to_numpy(),
        "series": model + "|" + region,
        "value": value.to_numpy(),
        "in_use": _in_use(rows, cols).to_numpy(),
    })


def _parse_outage(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    valid = _delivery_starts(rows, cols, date_names=("date", "operatingdate", "deliverydate"))
    return _melt(rows, _OUTAGE_COLUMNS, valid, posted, _in_use(rows, cols))


def _parse_keyed(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp,
                 key: str, value: str) -> pd.DataFrame:
    valid = _delivery_starts(rows, cols, date_names=("deliverydate",))
    series = rows[_require(cols, key)].astype(str).str.strip()
    amount = pd.to_numeric(rows[_require(cols, value)], errors="coerce")
    keep = ~series.str.lower().isin(["not applicable", "nan", ""])
    return pd.DataFrame({
        "posted_time": posted,
        "valid_time": valid.to_numpy(),
        "series": series.where(keep).to_numpy(),
        "value": amount.where(keep).to_numpy(),
        "in_use": _in_use(rows, cols).to_numpy(),
    }).dropna(subset=["series"])


def _parse_as_plan(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    return _parse_keyed(rows, cols, posted, "ancillarytype", "quantity")


def _parse_dam_mcpc(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    return _parse_keyed(rows, cols, posted, "ancillarytype", "mcpc")


def _parse_dam_spp(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    frame = _parse_keyed(rows, cols, posted, "settlementpoint", "settlementpointprice")
    if frame.empty:
        return frame
    point = frame["series"].astype(str).str.strip()
    return frame.loc[point.str.startswith(("LZ_", "HB_"))].assign(series=point)


def _interval_starts(rows: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    ending = _require(cols, "intervalending")
    flag = _col(cols, "intervalrepeatedhourflag", "intervalendingrepeatedhourflag")
    wall = pd.to_datetime(rows[ending], format="mixed", errors="coerce")
    flags = rows[flag] if flag else None
    return _to_utc(wall, flags) - pd.Timedelta(minutes=5)


def _parse_rtd_lmp(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    point_type = rows[_require(cols, "settlementpointtype")].astype(str).str.strip().str.upper()
    keep = point_type.isin(_RTD_POINT_TYPES)
    kept = rows.loc[keep]
    if kept.empty:
        return empty_frame()
    kept_cols = {_canon(c): c for c in kept.columns}
    valid = _interval_starts(kept, kept_cols)
    point = kept[_require(kept_cols, "settlementpoint")].astype(str).str.strip()
    value = pd.to_numeric(kept[_require(kept_cols, "lmp")], errors="coerce")
    return pd.DataFrame({
        "posted_time": posted,
        "valid_time": valid.to_numpy(),
        "series": point.to_numpy(),
        "value": value.to_numpy(),
        "in_use": pd.Series(pd.NA, index=kept.index, dtype="boolean").to_numpy(),
    })


def _parse_rtd_mcpc(rows: pd.DataFrame, cols: dict[str, str], posted: pd.Timestamp) -> pd.DataFrame:
    valid = _interval_starts(rows, cols)
    return _melt(rows, _RTD_PRODUCTS, valid, posted, _in_use(rows, cols))


_PARSERS = {
    "load": _parse_load,
    "wind_system": _parse_wind,
    "wind_region": _parse_wind,
    "wind_model": _parse_wind_model,
    "solar_system": _parse_solar,
    "solar_region": _parse_solar,
    "outage": _parse_outage,
    "as_plan": _parse_as_plan,
    "dam_mcpc": _parse_dam_mcpc,
    "dam_spp": _parse_dam_spp,
    "rtd_lmp": _parse_rtd_lmp,
    "rtd_mcpc": _parse_rtd_mcpc,
}
