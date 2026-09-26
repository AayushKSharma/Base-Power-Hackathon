import datetime as dt
import socket

import pandas as pd
import pytest

from conftest import FIXTURE_RAW, NetworkDisabled
from harness.market import (
    PriceThresholdScarcity,
    build_dataset,
    load_asdc,
    load_intervals,
    quality_report,
)

NORMAL = dt.date(2026, 9, 10)
SPRING_FORWARD = dt.date(2026, 3, 8)
SPIKE = dt.date(2026, 8, 26)
MISSING_FILE = dt.date(2026, 3, 19)
FALL_BACK = dt.date(2026, 11, 1)  # synthetic, see scripts/record_fixtures.py
FIXTURE_DAYS = (NORMAL, SPRING_FORWARD, SPIKE, MISSING_FILE, FALL_BACK)


def build_fixture_days(out):
    for day in FIXTURE_DAYS:
        build_dataset(day, day, raw_dir=FIXTURE_RAW, store_dir=out, fetch=False)


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    out = tmp_path_factory.mktemp("market")
    build_fixture_days(out)
    return out


def one_day(store, day):
    return load_intervals(day, day, store_dir=store)


def cpt_rows(df, *hhmm, repeated=False):
    """Rows whose CPT wall-clock start is one of `hhmm`."""
    local = df["interval_start_cpt"]
    keep = local.dt.strftime("%H:%M").isin(hhmm) & (df["repeated_hour"] == repeated)
    return df[keep]


def test_normal_day_has_288_five_minute_intervals(store):
    df = load_intervals(NORMAL, NORMAL, store_dir=store)

    assert len(df) == 288
    assert df.index.name == "interval_start_utc"
    assert df.index[0] == pd.Timestamp("2026-09-10 05:00", tz="UTC")
    assert set(df.index[1:] - df.index[:-1]) == {pd.Timedelta(minutes=5)}


def test_spring_forward_day_has_276_intervals_and_skips_2am(store):
    df = one_day(store, SPRING_FORWARD)

    assert len(df) == 276
    assert set(df.index[1:] - df.index[:-1]) == {pd.Timedelta(minutes=5)}
    assert not (df["interval_start_cpt"].dt.hour == 2).any()
    assert cpt_rows(df, "01:55")["cpt_dst"].tolist() == [False]
    assert cpt_rows(df, "03:00")["cpt_dst"].tolist() == [True]


def test_fall_back_day_has_300_intervals_with_the_repeated_hour_marked(store):
    df = one_day(store, FALL_BACK)

    assert len(df) == 300
    assert set(df.index[1:] - df.index[:-1]) == {pd.Timedelta(minutes=5)}
    first, second = cpt_rows(df, "01:30"), cpt_rows(df, "01:30", repeated=True)
    assert first.index.tolist() == [pd.Timestamp("2026-11-01 06:30", tz="UTC")]
    assert second.index.tolist() == [pd.Timestamp("2026-11-01 07:30", tz="UTC")]
    assert first["cpt_dst"].tolist() == [True]
    assert second["cpt_dst"].tolist() == [False]


def test_sced_mcpc_lands_on_the_interval_its_run_started_in(store):
    df = one_day(store, SPIKE)

    rows = cpt_rows(df, "22:15", "22:20", "22:25")

    assert rows["rt_mcpc_5m_ecrs"].tolist() == [709.53, 821.83, 589.25]


def test_each_15_minute_price_appears_on_exactly_its_three_5_minute_rows(store):
    df = one_day(store, SPIKE)

    rows = cpt_rows(df, "17:10", "17:15", "17:20", "17:25", "17:30")

    # NP6-331-CD HE18: interval 1 = 0.16, interval 2 = 0.18, interval 3 = 0.14
    assert rows["rt_mcpc_15m_nspin"].tolist() == [0.16, 0.18, 0.18, 0.18, 0.14]


def test_repeated_hour_prices_stay_on_their_own_utc_rows(store):
    df = one_day(store, FALL_BACK)

    first = cpt_rows(df, "01:15")
    second = cpt_rows(df, "01:15", repeated=True)

    # The synthetic second pass carries prices $100 above the first.
    assert second["rt_mcpc_15m_nspin"].item() == pytest.approx(first["rt_mcpc_15m_nspin"].item() + 100)
    assert second["rt_mcpc_5m_nspin"].item() == pytest.approx(first["rt_mcpc_5m_nspin"].item() + 100)


def test_each_day_ahead_price_appears_on_exactly_its_twelve_5_minute_rows(store):
    df = one_day(store, SPIKE)

    # NP4-188-CD NSPIN: HE17 = 0.74, HE18 (17:00-18:00) = 0.81, HE19 = 1.00
    he18 = [f"17:{m:02d}" for m in range(0, 60, 5)]
    assert cpt_rows(df, *he18)["dam_mcpc_nspin"].tolist() == [0.81] * 12
    assert cpt_rows(df, "16:55", "18:00")["dam_mcpc_nspin"].tolist() == [0.74, 1.00]


def test_spring_forward_hourly_prices_skip_the_missing_hour(store):
    df = one_day(store, SPRING_FORWARD)

    assert df["dam_mcpc_regup"].notna().all()
    assert (df["q_dam_mcpc_regup"] == "ok").all()


LZ_COLUMNS = ["lz_spp_houston", "lz_spp_north", "lz_spp_south", "lz_spp_west"]


def test_load_zone_prices_are_present_and_in_ercot_range(store):
    df = load_intervals(SPIKE, SPIKE, store_dir=store)

    assert df[LZ_COLUMNS].notna().all().all()
    # ERCOT RT energy prices sit between the -$251 floor and the $5,000 cap.
    assert df[LZ_COLUMNS].min().min() >= -251
    assert df[LZ_COLUMNS].max().max() <= 5000


def test_load_zone_price_is_the_zones_own_15_minute_spp(store):
    df = one_day(store, SPIKE)

    # NP6-905-CD HE18: LZ_HOUSTON interval 1 = 122.02; LZ_WEST interval 3 = 118.83
    assert cpt_rows(df, "17:00", "17:05", "17:10")["lz_spp_houston"].tolist() == [122.02] * 3
    assert cpt_rows(df, "17:30", "17:35", "17:40")["lz_spp_west"].tolist() == [118.83] * 3


def test_a_missing_source_file_is_flagged_not_filled(store):
    df = one_day(store, MISSING_FILE)  # NP6-331-CD left out of the fixtures

    assert df["rt_mcpc_15m_ecrs"].isna().all()
    assert (df["q_rt_mcpc_15m_ecrs"] == "no_source").all()
    assert (df["q_dam_mcpc_ecrs"] == "ok").all()


def test_a_sced_gap_is_flagged_not_forward_filled(store):
    df = one_day(store, MISSING_FILE)  # real SCED gap: runs at 10:30:17, then 10:47:00

    rows = cpt_rows(df, "10:30", "10:35", "10:40", "10:45")

    assert rows["q_rt_mcpc_5m_ecrs"].tolist() == ["ok", "gap", "gap", "ok"]
    assert rows["rt_mcpc_5m_ecrs"].isna().tolist() == [False, True, True, False]


def test_as_capability_time_weights_two_sced_runs_in_one_interval(store):
    df = one_day(store, MISSING_FILE)

    # NP6-328-CD CapREGUPTotal: 22130.6236 MW from 10:47:00 (138 s), then
    # 22083.858 MW from 10:49:18 (42 s) until the interval ends at 10:50.
    assert cpt_rows(df, "10:45")["as_cap_regup"].item() == pytest.approx(22119.7116)
    assert cpt_rows(df, "10:30")["as_cap_regup"].item() == pytest.approx(22075.0921)


def test_rebuilding_the_same_range_gives_identical_output(store, tmp_path):
    before = load_intervals(SPIKE, SPIKE, store_dir=store)
    build_dataset(SPIKE, SPIKE, raw_dir=FIXTURE_RAW, store_dir=store, fetch=False)
    fresh = tmp_path / "fresh"
    build_fixture_days(fresh)

    pd.testing.assert_frame_equal(load_intervals(SPIKE, SPIKE, store_dir=store), before)
    pd.testing.assert_frame_equal(load_intervals(SPIKE, SPIKE, store_dir=fresh), before)


def test_loader_works_with_the_network_disabled(store):
    with pytest.raises(NetworkDisabled):
        socket.create_connection(("www.ercot.com", 443))

    df = load_intervals(NORMAL, NORMAL, store_dir=store)

    assert len(df) == 288


def test_a_multi_day_range_is_one_contiguous_utc_index(tmp_path):
    next_day = SPRING_FORWARD + dt.timedelta(days=1)  # no raw files for this day
    build_dataset(SPRING_FORWARD, next_day, raw_dir=FIXTURE_RAW, store_dir=tmp_path, fetch=False)

    df = load_intervals(SPRING_FORWARD, next_day, store_dir=tmp_path)

    assert len(df) == 276 + 288
    assert set(df.index[1:] - df.index[:-1]) == {pd.Timedelta(minutes=5)}
    assert (df.loc[df["operating_day"] == pd.Timestamp(next_day), "q_rt_mcpc_5m_ecrs"] == "no_source").all()


def test_loading_days_that_were_never_built_raises(store):
    with pytest.raises(LookupError, match="2026-03-10"):
        load_intervals(dt.date(2026, 3, 9), dt.date(2026, 3, 10), store_dir=store)


def test_scarcity_flag_marks_exactly_the_intervals_above_a_fixed_threshold(store):
    proxy = PriceThresholdScarcity(thresholds={"ecrs": 500.0})

    df = load_intervals(SPIKE, SPIKE, store_dir=store, scarcity=proxy)

    # NP6-332-CD ECRS on 2026-08-26 was above $500/MW-h for these SCED runs only.
    scarce = df.loc[df["scarce_ecrs"].astype(bool), "interval_start_cpt"].dt.strftime("%H:%M")
    assert scarce.tolist() == ["22:05", "22:10", "22:15", "22:20", "22:25", "22:30"]


def test_default_scarcity_is_a_high_percentile_of_price_history(store):
    days = sorted(FIXTURE_DAYS)
    df = pd.concat([load_intervals(d, d, store_dir=store) for d in days])

    for product in ["regup", "regdn", "rrs", "ecrs", "nspin"]:
        flagged = df[f"scarce_{product}"].sum()
        priced = df[f"rt_mcpc_5m_{product}"].notna().sum()
        # At most 1% of priced intervals (+1 for the interpolated percentile).
        assert 0 < flagged <= 0.01 * priced + 1


def test_scarcity_is_unknown_where_the_price_is_missing(store):
    df = one_day(store, MISSING_FILE)

    assert cpt_rows(df, "10:35")["scarce_ecrs"].isna().all()
    assert cpt_rows(df, "10:30")["scarce_ecrs"].notna().all()


def test_scarcity_proxy_can_be_swapped(store):
    class LowCapability:
        """Scarce when system ECRS capability drops below 10 GW."""

        def flags(self, intervals, history):
            return pd.DataFrame({"ecrs": intervals["as_cap_ecrs"] < 10_000}, index=intervals.index)

    df = load_intervals(SPRING_FORWARD, SPRING_FORWARD, store_dir=store, scarcity=LowCapability())

    assert (df["scarce_ecrs"] == (df["as_cap_ecrs"] < 10_000)).all()
    assert df["scarce_ecrs"].any()


def test_demand_curves_are_an_hourly_table_per_product(store):
    asdc = load_asdc(NORMAL, NORMAL, store_dir=store)

    assert asdc["interval_start_utc"].nunique() == 24
    assert set(asdc["product"]) == {"regup", "regdn", "rrs", "ecrs", "nspin"}
    # NP4-212-CD HE18 (17:00 CDT) Reg-Down: flat at $5,000 up to 697 MW.
    regdn = asdc[(asdc["product"] == "regdn")
                 & (asdc["interval_start_utc"] == pd.Timestamp("2026-09-10 22:00", tz="UTC"))]
    assert regdn[["point", "quantity_mw", "price"]].values.tolist() == [[1, 0, 5000], [2, 697, 5000]]


def test_days_without_published_demand_curves_have_no_rows(store):
    asdc = load_asdc(SPRING_FORWARD, SPRING_FORWARD, store_dir=store)

    assert asdc.empty
    assert list(asdc.columns) == [
        "interval_start_utc", "interval_start_cpt", "product", "point", "quantity_mw", "price",
    ]


def test_building_before_rtc_b_is_refused(tmp_path):
    with pytest.raises(ValueError, match="2025-12-05"):
        build_dataset(dt.date(2025, 12, 4), dt.date(2025, 12, 5),
                      raw_dir=FIXTURE_RAW, store_dir=tmp_path, fetch=False)


@pytest.fixture(scope="module")
def report(store):
    return quality_report(store_dir=store, raw_dir=FIXTURE_RAW)


def test_quality_report_gives_coverage_per_field(report):
    fields = report.fields

    # 15-min file missing on 1 of 5 days; SCED gap of 2 intervals on 2026-03-19.
    assert fields.loc["rt_mcpc_15m_ecrs", "no_source_days"] == [MISSING_FILE]
    assert fields.loc["rt_mcpc_5m_ecrs", "gap"] == 2
    assert fields.loc["rt_mcpc_5m_ecrs", "coverage"] == pytest.approx(1 - 2 / 1440)
    assert fields.loc["lz_spp_houston", "unit"] == "$/MWh"
    assert fields.loc["rt_mcpc_5m_ecrs", "report"] == "NP6-332-CD"


def test_quality_report_lists_uncovered_days_and_gaps_per_source(report):
    assert (MISSING_FILE, MISSING_FILE) in report.uncovered["NP6-331-CD"]
    cpt = [(a.strftime("%Y-%m-%d %H:%M"), b.strftime("%H:%M")) for a, b in report.gaps["NP6-332-CD"]]
    assert ("2026-03-19 10:35", "10:45") in cpt
    assert report.uncovered["NP4-212-CD"][0] == (SPRING_FORWARD, SPRING_FORWARD)


def test_quality_report_summarises_the_dataset(report):
    assert (report.start, report.end) == (SPRING_FORWARD, FALL_BACK)
    assert (report.days, report.rows) == (5, 1440)
    assert report.prices.loc["rt_mcpc_5m_ecrs", "max"] == 821.83
    assert report.scarcity.loc["ecrs", "intervals"] > 0
    text = report.to_markdown()
    assert "2026-03-08" in text and "821.83" in text
