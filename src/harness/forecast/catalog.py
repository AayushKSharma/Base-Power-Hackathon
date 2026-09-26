"""Forecast inputs the point-in-time store is built from.

Each input is one ERCOT report. A stored row is one vintage of one series:
posted time (when ERCOT published it) and valid time (the hour or interval it
describes) are kept together and never collapsed across postings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# How far ahead of the decision time `as_of` returns valid times, unless the caller says otherwise.
DEFAULT_HORIZON = dt.timedelta(hours=168)

# How far apart successive postings can be before the coverage report calls it a gap.
HOURLY = "hourly"
DAILY = "daily"
FIVE_MIN = "five_min"


@dataclass(frozen=True)
class ForecastInput:
    """One forecast input and the report it is parsed from."""

    id: str
    report_id: str
    title: str
    # MIS reportTypeId of the live report. MIS keeps roughly the last week.
    live_type_id: int
    # Public API path. Archive download filters on postDatetime. None when the
    # report is not on the public API; the store then covers only what MIS keeps.
    api_endpoint: str | None
    cadence: str
    # Which parser turns the report's CSV into posted/valid/series rows.
    kind: str
    unit: str


INPUTS: tuple[ForecastInput, ...] = (
    ForecastInput(
        "load_by_model_zone", "NP3-565-CD",
        "Seven-day load forecast by model and weather zone",
        14837, "/np3-565-cd/lf_by_model_weather_zone", HOURLY, "load", "MW",
    ),
    ForecastInput(
        "wind_system", "NP4-732-CD",
        "Wind power production, hourly actual and forecast, system and load zones",
        13028, "/np4-732-cd/wpp_hrly_avrg_actl_fcast", HOURLY, "wind_system", "MW",
    ),
    ForecastInput(
        "wind_region", "NP4-742-CD",
        "Wind power production, hourly actual and forecast, by geographical region",
        14787, "/np4-742-cd/wpp_hrly_actual_fcast_geo", HOURLY, "wind_region", "MW",
    ),
    ForecastInput(
        "wind_by_model", "NP4-442-CD",
        "Hourly system-wide and regional wind forecasts by model",
        19385, "/np4-442-cd/hrly_sys_reg_wind_fcast_model", HOURLY, "wind_model", "MW",
    ),
    ForecastInput(
        "solar_system", "NP4-737-CD",
        "Solar power production, hourly actual and forecast, system-wide",
        13483, "/np4-737-cd/spp_hrly_avrg_actl_fcast", HOURLY, "solar_system", "MW",
    ),
    ForecastInput(
        "solar_region", "NP4-745-CD",
        "Solar power production, hourly actual and forecast, by geographical region",
        21809, "/np4-745-cd/spp_hrly_actual_fcast_geo", HOURLY, "solar_region", "MW",
    ),
    ForecastInput(
        "outage_capacity", "NP3-233-CD",
        "Hourly resource outage capacity",
        13103, "/np3-233-cd/hourly_res_outage_cap", HOURLY, "outage", "MW",
    ),
    ForecastInput(
        "dam_as_plan", "NP4-33-CD",
        "Day-ahead ancillary service plan",
        12316, "/np4-33-cd/dam_as_plan", DAILY, "as_plan", "MW",
    ),
    ForecastInput(
        "dam_spp", "NP4-190-CD",
        "Day-ahead settlement point prices (load zones and hubs)",
        12331, "/np4-190-cd/dam_stlmnt_pnt_prices", DAILY, "dam_spp", "$/MWh",
    ),
    ForecastInput(
        "dam_mcpc", "NP4-188-CD",
        "Day-ahead clearing prices for capacity",
        12329, "/np4-188-cd/dam_clear_price_for_cap", DAILY, "dam_mcpc", "$/MW-h",
    ),
    ForecastInput(
        "rtd_lmp", "NP6-970-CD",
        "RTD indicative LMPs for load zones and hubs",
        13073, "/np6-970-cd/rtd_lmp_node_zone_hub", FIVE_MIN, "rtd_lmp", "$/MWh",
    ),
    ForecastInput(
        "rtd_mcpc", "NP6-329-CD",
        "RTD indicative real-time MCPC",
        24889, "/np6-329-cd/", FIVE_MIN, "rtd_mcpc", "$/MW-h",
    ),
)

BY_ID: dict[str, ForecastInput] = {item.id: item for item in INPUTS}
