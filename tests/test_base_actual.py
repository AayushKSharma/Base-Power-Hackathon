import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from harness.base_actual import build_base_actual, daily_summary, dispatch_down_events, load_base_actual
from harness.market import MarketDataMissing

RAW = Path(__file__).parent / "fixtures" / "base_actual_raw"
DAY = dt.date(2026, 7, 20)  # NP3-965-ER, published 2026-09-18
PUBLISHED_BY = dt.date(2026, 9, 19)
BASE = ["MIDNT_ALD1", "OB_ALD1", "SANSM_ALD1"]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    out = tmp_path_factory.mktemp("base_actual")
    build_base_actual(DAY, DAY, raw_dir=RAW, store_dir=out, fetch=False, today=PUBLISHED_BY)
    return out


def at(df, resource, hhmmss):
    rows = df[(df["resource"] == resource) & (df["sced_time_cpt"].dt.strftime("%H:%M:%S") == hhmmss)]
    assert len(rows) == 1
    return rows.iloc[0]


def test_only_aggregate_load_resources_are_stored(store):
    df = load_base_actual(DAY, DAY, store_dir=store)

    assert df["resource"].str.contains(r"_ALD\d+$").all()
    assert len(df) == 13 * 288
    base = df[df["qse"] == "QBASTX"]
    assert sorted(base["resource"].unique()) == BASE


def test_derived_fields_are_exact(store):
    df = load_base_actual(DAY, DAY, store_dir=store)

    # NP3-965-ER, OB_ALD1 at 20:20:22 CDT: RPC 60.4, LPC 13.1, Base Point 35.4104309082031
    row = at(df, "OB_ALD1", "20:20:22")
    assert row["flexible_mw"] == pytest.approx(47.3)
    assert row["deviation_mw"] == pytest.approx(24.9895690917969)


def test_stored_columns_are_the_listed_fields(store):
    df = load_base_actual(DAY, DAY, store_dir=store)

    assert list(df.columns) == [
        "sced_time_utc", "sced_time_cpt", "interval_start_utc", "operating_day", "repeated_hour",
        "qse", "dme", "resource", "status",
        "max_power_consumption_mw", "low_power_consumption_mw", "real_power_consumption_mw",
        "base_point_mw", "as_capability_ecrs_mw", "as_capability_nspin_mw",
        "as_award_ecrs_mw", "as_award_nspin_mw", "self_provided_ecrs_mw",
        "flexible_mw", "deviation_mw",
    ]


def test_an_empty_award_cell_means_no_award(store):
    df = load_base_actual(DAY, DAY, store_dir=store)

    # OB_ALD1 01:00:23: ECRS award cell empty, Non-Spin 12.2
    row = at(df, "OB_ALD1", "01:00:23")
    assert (row["as_award_ecrs_mw"], row["as_award_nspin_mw"]) == (0.0, 12.2)


def base_events(store, threshold_mw):
    df = load_base_actual(DAY, DAY, store_dir=store)
    events = dispatch_down_events(df[df["qse"] == "QBASTX"], threshold_mw=threshold_mw)
    return list(zip(events["resource"], events["start_cpt"].dt.strftime("%H:%M:%S"),
                    events["end_cpt"].dt.strftime("%H:%M:%S"), events["runs"]))


def test_dispatch_down_events_on_the_known_day(store):
    # SCED set Base Point below consumption: OB_ALD1 by 24.99 at 20:20:22 and 24.17 at
    # 20:25:20; SANSM_ALD1 by 6.10 at 20:20:22. Nothing else deviates by more than 2 MW.
    assert base_events(store, threshold_mw=5.0) == [
        ("OB_ALD1", "20:20:22", "20:25:20", 2),
        ("SANSM_ALD1", "20:20:22", "20:20:22", 1),
    ]


def test_dispatch_down_threshold_is_configurable(store):
    assert base_events(store, threshold_mw=10.0) == [("OB_ALD1", "20:20:22", "20:25:20", 2)]
    assert base_events(store, threshold_mw=25.0) == []


def test_daily_summary_matches_the_independent_one_day_look(store):
    df = load_base_actual(DAY, DAY, store_dir=store)

    summary = daily_summary(df[df["qse"] == "QBASTX"], threshold_mw=5.0)

    # Computed independently from the raw fixture with awk (mean of RPC - LPC, max awards,
    # mean |RPC - Base Point|). The research note's one-day look agrees to 0.1 MW except
    # for its "—" ECRS entries: SANSM_ALD1 and MIDNT_ALD1 held ECRS in 124 and 288 runs.
    expected = {
        "OB_ALD1": (47.5781, 24.3, 12.2, 0.5479, 1),
        "SANSM_ALD1": (21.4274, 21.0, 7.0, 0.1376, 1),
        "MIDNT_ALD1": (6.5490, 6.4, 0.0, 0.0112, 0),
    }
    by_resource = summary.reset_index(level="operating_day", drop=True).to_dict(orient="index")
    for resource, (flex, ecrs, nspin, dev, events) in expected.items():
        row = by_resource[resource]
        assert row["mean_flexible_mw"] == pytest.approx(flex, abs=1e-4)
        assert row["max_ecrs_award_mw"] == pytest.approx(ecrs)
        assert row["max_nspin_award_mw"] == pytest.approx(nspin)
        assert row["mean_abs_deviation_mw"] == pytest.approx(dev, abs=1e-4)
        assert row["dispatch_down_events"] == events


def test_days_not_yet_published_are_reported_not_errors(tmp_path):
    # With the network disabled: 2026-07-21 is published on 2026-09-19, after "today".
    result = build_base_actual(DAY, DAY + dt.timedelta(days=1), raw_dir=RAW, store_dir=tmp_path,
                               fetch=True, today=PUBLISHED_BY - dt.timedelta(days=1))

    assert result.built == [DAY]
    assert result.unpublished == [DAY + dt.timedelta(days=1)]
    assert result.errors == []


def test_rebuilding_a_day_gives_identical_output(store, tmp_path):
    before = load_base_actual(DAY, DAY, store_dir=store)
    build_base_actual(DAY, DAY, raw_dir=RAW, store_dir=store, fetch=False, today=PUBLISHED_BY)
    build_base_actual(DAY, DAY, raw_dir=RAW, store_dir=tmp_path, fetch=False, today=PUBLISHED_BY)

    pd.testing.assert_frame_equal(load_base_actual(DAY, DAY, store_dir=store), before)
    pd.testing.assert_frame_equal(load_base_actual(DAY, DAY, store_dir=tmp_path), before)


def test_loading_a_day_that_was_not_ingested_raises(store):
    with pytest.raises(MarketDataMissing, match="2026-07-21"):
        load_base_actual(DAY, DAY + dt.timedelta(days=1), store_dir=store)


def test_loader_filters_by_resource_or_qse(store):
    one = load_base_actual(DAY, DAY, store_dir=store, resources=["OB_ALD1"])
    base = load_base_actual(DAY, DAY, store_dir=store, qse="QBASTX")

    assert set(one["resource"]) == {"OB_ALD1"} and len(one) == 288
    assert sorted(base["resource"].unique()) == BASE
