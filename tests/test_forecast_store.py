"""Point-in-time forecast store: as_of never looks ahead, and a run sees that view."""

import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import SPRING_FORWARD, fleet_scenario
from harness import run
from harness.forecast import ForecastStore, as_of, build_forecasts, load_forecasts
from harness.forecast.catalog import INPUTS
from harness.forecast.raw import ForecastRaw
from harness.forecast.sources import ApiForecastRoute, MisForecastRoute
from harness.market import load_intervals
from harness.market.catalog import CPT
from harness.market.sources import Doc

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "forecast"
DAY = dt.date(2026, 3, 8)
HORIZON = dt.timedelta(hours=6)
AT_0800 = pd.Timestamp("2026-03-08 08:00", tz="UTC")
AT_0900 = pd.Timestamp("2026-03-08 09:00", tz="UTC")
AT_1000 = pd.Timestamp("2026-03-08 10:00", tz="UTC")


def build(store):
    return build_forecasts(DAY, DAY, raw_dir=FIXTURE_RAW, store_dir=store, fetch=False)


def posted_times(result):
    return [pd.Timestamp(row["posted_time"]) for rows in result.values() for row in rows]


def series_rows(result, input_id, series, valid):
    return [
        row for row in result.get(input_id, [])
        if row["series"] == series and pd.Timestamp(row["valid_time"]) == valid
    ]


@pytest.fixture
def store(tmp_path):
    build(tmp_path)
    return tmp_path


def test_as_of_never_returns_a_row_posted_after_the_decision_time(store):
    rng = np.random.default_rng(23)
    start = pd.Timestamp("2026-03-08 05:59", tz="UTC")
    end = pd.Timestamp("2026-03-08 12:00", tz="UTC")

    moments = [start, AT_0800, AT_0900, AT_1000]
    moments += [start + (end - start) * float(rng.random()) for _ in range(40)]
    for moment in moments:
        for posted in posted_times(as_of(moment, horizon=HORIZON, store_dir=store)):
            assert posted <= moment


def test_as_of_returns_the_latest_vintage_posted_at_or_before_the_decision(store):
    early = as_of(AT_0800, inputs=["dam_as_plan", "wind_by_model"], horizon=HORIZON, store_dir=store)
    later = as_of(AT_0900, inputs=["dam_as_plan"], horizon=HORIZON, store_dir=store)
    after_wind = as_of(AT_1000, inputs=["wind_by_model"], horizon=HORIZON, store_dir=store)

    # 07:00Z vintage: ECRS HE04 (08:00Z) is 100. The 09:00Z vintage's 900 is not visible yet.
    ecrs = series_rows(early, "dam_as_plan", "ECRS", AT_0800)
    assert [row["value"] for row in ecrs] == [100.0]
    assert ecrs[0]["posted_time"].startswith("2026-03-08T07:00:00")
    assert series_rows(early, "dam_as_plan", "ECRS", AT_0900)[0]["value"] == 110.0
    # HE24 is 20 hours ahead, outside a 6-hour horizon.
    assert all(row["value"] != 1.0 for row in early["dam_as_plan"])
    # In-use model S is 50.5; model A, posted in the same vintage, is kept too.
    assert series_rows(early, "wind_by_model", "S|SYSTEM_TOTAL", AT_0800)[0]["value"] == 50.5
    unused = series_rows(early, "wind_by_model", "A|SYSTEM_TOTAL", AT_0800)
    assert [row["value"] for row in unused] == [40.0]
    assert unused[0]["in_use"] is False
    # Exactly at the next DAM posting, that vintage replaces the earlier one.
    assert series_rows(later, "dam_as_plan", "ECRS", AT_0900)[0]["value"] == 910.0
    assert series_rows(later, "dam_as_plan", "ECRS", AT_0800) == []
    # Wind model reposted at 09:30Z; a 10:00Z decision sees 78, not 51.
    assert series_rows(after_wind, "wind_by_model", "S|SYSTEM_TOTAL", AT_1000)[0]["value"] == 78.0


def test_every_stored_row_keeps_its_own_posted_time_and_valid_time(store):
    for spec in INPUTS:
        frame = load_forecasts(spec.id, store_dir=store)

        assert list(frame.columns) == ["posted_time", "valid_time", "series", "value", "in_use"]
        assert frame["posted_time"].notna().all()
        assert frame["valid_time"].notna().all()
        assert len(frame) > 0

    plan = load_forecasts("dam_as_plan", store_dir=store)
    same_hour = plan[(plan["series"] == "ECRS") & (plan["valid_time"] == AT_0800)]
    assert sorted(same_hour["value"].tolist()) == [100.0, 900.0]
    assert same_hour["posted_time"].nunique() == 2


def test_as_of_drops_rows_that_are_not_forecast_inputs_for_this_store(store):
    prices = as_of(AT_0800, inputs=["dam_spp"], horizon=HORIZON, store_dir=store)
    lmps = as_of(AT_0800, inputs=["rtd_lmp"], horizon=HORIZON, store_dir=store)

    assert sorted(row["series"] for row in prices["dam_spp"]) == ["HB_HOUSTON", "LZ_HOUSTON"]
    assert {row["series"]: row["value"] for row in prices["dam_spp"]} == {
        "LZ_HOUSTON": 67.31, "HB_HOUSTON": 20.5,
    }
    assert {row["series"]: row["value"] for row in lmps["rtd_lmp"]} == {
        "LZ_HOUSTON": 33.3, "HB_NORTH": 21.0,
    }


def test_other_inputs_land_on_the_hour_the_fixture_names(store):
    moment = AT_0800
    view = as_of(moment, horizon=HORIZON, store_dir=store)

    assert series_rows(view, "load_by_model_zone", "A3|system", moment)[0]["value"] == 11.6
    assert series_rows(view, "load_by_model_zone", "A3|coast", moment)[0]["in_use"] is True
    assert series_rows(view, "wind_system", "stwpf|system", moment)[0]["value"] == 1234.5
    assert series_rows(view, "wind_region", "stwpf|panhandle", moment)[0]["value"] == 3.5
    assert series_rows(view, "solar_system", "stppf|system", moment)[0]["value"] == 5.5
    assert series_rows(view, "solar_region", "stppf|center_west", moment)[0]["value"] == 0.25
    assert series_rows(view, "outage_capacity", "total_resource|south", moment)[0]["value"] == 10.0
    assert series_rows(view, "dam_mcpc", "ECRS", moment)[0]["value"] == 1.25
    assert series_rows(view, "rtd_mcpc", "ECRS", moment)[0]["value"] == 0.13


def test_an_unknown_input_is_rejected(store):
    with pytest.raises(ValueError, match="weather"):
        as_of(AT_0800, inputs=["weather"], store_dir=store)


def test_rebuilding_the_same_posted_dates_gives_identical_rows(store, tmp_path):
    before = {spec.id: load_forecasts(spec.id, store_dir=store) for spec in INPUTS}
    build(store)
    fresh = tmp_path / "fresh"
    build(fresh)

    for spec in INPUTS:
        again = load_forecasts(spec.id, store_dir=store)
        other = load_forecasts(spec.id, store_dir=fresh)
        pd.testing.assert_frame_equal(again, before[spec.id])
        pd.testing.assert_frame_equal(other, before[spec.id])


def test_a_cached_mis_document_is_not_downloaded_again(tmp_path):
    raw = tmp_path / "raw"
    store = tmp_path / "store"
    payload = _zip("DeliveryDate,HourEnding,AncillaryType,Quantity,DSTFlag\n03/08/2026,04:00,ECRS,42,Y\n")
    fake = _Mis(payload)
    route = MisForecastRoute(fake, ForecastRaw(raw))

    build_forecasts(DAY, DAY, raw_dir=raw, store_dir=store, routes=[route])
    build_forecasts(DAY, DAY, raw_dir=raw, store_dir=store, routes=[route])

    assert fake.downloads == 1
    rows = as_of(AT_0800, inputs=["dam_as_plan"], horizon=HORIZON, store_dir=store)
    assert rows["dam_as_plan"][0]["value"] == 42.0


def test_a_naive_api_post_datetime_is_central_time_not_utc(tmp_path, monkeypatch):
    """01:00 CPT on 2026-03-08 is 07:00 UTC. Reading it as UTC would publish it at 01:00 UTC."""
    for name in ("ERCOT_API_USERNAME", "ERCOT_API_PASSWORD", "ERCOT_PUBLIC_API_SUBSCRIPTION_KEY"):
        monkeypatch.setenv(name, "not-a-real-secret")
    raw, store = tmp_path / "raw", tmp_path / "store"
    route = ApiForecastRoute(ForecastRaw(raw))
    route._client = _ApiTable()

    build_forecasts(DAY, DAY, raw_dir=raw, store_dir=store, routes=[route])

    hidden = as_of(pd.Timestamp("2026-03-08 02:00", tz="UTC"), inputs=["dam_mcpc"],
                   horizon=HORIZON, store_dir=store)
    visible = as_of(pd.Timestamp("2026-03-08 07:00", tz="UTC"), inputs=["dam_mcpc"],
                    horizon=HORIZON, store_dir=store)
    assert hidden == {}
    assert series_rows(visible, "dam_mcpc", "ECRS", AT_0800)[0]["value"] == 1.5
    assert series_rows(visible, "dam_mcpc", "ECRS", AT_0800)[0]["posted_time"].startswith("2026-03-08T07:00:00")


def test_the_coverage_report_names_the_posted_range_and_the_gaps(store):
    report = build(store).report
    by_id = {item.spec.id: item for item in report.inputs}

    assert [item.spec.id for item in report.inputs] == [spec.id for spec in INPUTS]
    wind = by_id["wind_by_model"]
    assert wind.vintages == 2
    assert wind.gaps == (
        (pd.Timestamp("2026-03-08 06:30", tz="UTC"), pd.Timestamp("2026-03-08 09:30", tz="UTC")),
    )
    assert wind.first_posted == pd.Timestamp("2026-03-08 06:30", tz="UTC")
    assert wind.last_posted == pd.Timestamp("2026-03-08 09:30", tz="UTC")
    # One posted day was requested and both vintages fall on it, so no uncovered date.
    assert by_id["load_by_model_zone"].uncovered == ()
    text = report.to_markdown()
    assert "NP4-442-CD" in text
    if not report.api_credentials:
        assert "credentials are not set" in text


class _Recorder:
    name = "recorder"

    def __init__(self):
        self.seen = []

    def decide(self, observation):
        self.seen.append(observation)
        return {"ECRS": 0.0, "NONSPIN": 0.0}


def test_a_policy_run_with_forecasts_receives_the_as_of_rows_for_each_decision(store, market_store):
    policy = _Recorder()

    run(policy, _scenario(), SPRING_FORWARD, SPRING_FORWARD, seed=1,
        market=lambda start, end: load_intervals(start, end, store_dir=market_store),
        forecasts=ForecastStore(store),
        forecast_horizon=HORIZON)

    at_0800 = next(obs for obs in policy.seen if obs["now"]["interval_start_utc"] == "2026-03-08T08:00:00+00:00")
    at_1000 = next(obs for obs in policy.seen if obs["now"]["interval_start_utc"] == "2026-03-08T10:00:00+00:00")

    assert series_rows(at_0800["forecasts"], "dam_as_plan", "ECRS", AT_0800)[0]["value"] == 100.0
    assert series_rows(at_1000["forecasts"], "wind_by_model", "S|SYSTEM_TOTAL", AT_1000)[0]["value"] == 78.0
    for obs in (at_0800, at_1000):
        decision = pd.Timestamp(obs["now"]["interval_start_utc"])
        for posted in posted_times(obs["forecasts"]):
            assert posted <= decision
        assert json.loads(json.dumps(obs)) == obs


def _scenario():
    return fleet_scenario()


class _Mis:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.downloads = 0

    def list_docs(self, report_type_id: int) -> list[Doc]:
        # Only the DAM AS plan report is on this fake MIS. The other inputs have nothing.
        if report_type_id != 12316:
            return []
        return [Doc("1", "ASPLANNP433_csv", pd.Timestamp("2026-03-08 01:00", tz=CPT))]

    def download(self, doc: Doc) -> bytes:
        self.downloads += 1
        return self.payload


class _ApiTable:
    """Stands in for ErcotAPI.get_historical_data. postDatetime has no offset."""

    def get_historical_data(self, endpoint: str, **kwargs: object) -> pd.DataFrame:
        if "np4-188" not in endpoint:
            return pd.DataFrame()
        return pd.DataFrame({
            "DeliveryDate": ["03/08/2026"],
            "HourEnding": ["04:00"],
            "AncillaryType": ["ECRS"],
            "MCPC": ["1.5"],
            "DSTFlag": ["Y"],
            "postDatetime": ["2026-03-08 01:00:00"],
        })


def _zip(csv: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("as_plan.csv", csv)
    return buf.getvalue()
