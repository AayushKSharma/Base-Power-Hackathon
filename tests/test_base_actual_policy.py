"""Base actual as a scored policy, on the recorded 2026-07-20 disclosure.

The awards below are the three Base resources on that day, summed per interval.
They are taken from the raw fixture, not recomputed by the test.

Delivery grading compares Real Power Consumption with Base Point on dispatch-down
events. OB_ALD1's event is the two runs at 20:20:22 (RPC 60.4, Base Point
35.4104309082031) and 20:25:20 (RPC 37.3, Base Point 13.1304626464844).
SANSM_ALD1's event is the single run at 20:20:22 (RPC 30.2, Base Point
24.0999755859375).
"""

import datetime as dt
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

from harness.base_actual import build_base_actual
from harness.cli import main
from harness.market.catalog import FIELDS, quality_column
from harness.market.store import MarketStore

RAW = Path(__file__).parent / "fixtures" / "base_actual_raw"
DAY = dt.date(2026, 7, 20)
PUBLISHED_BY = dt.date(2026, 9, 19)
# 01:00:23 CDT: MIDNT ECRS 6.4; OB Non-Spin 12.2; SANSM Non-Spin 7.0.
HOUR_1 = "2026-07-20T06:00:00+00:00"
# 20:20:22 CDT: MIDNT ECRS 6.4; SANSM ECRS 0.2; no Non-Spin.
DISPATCH = "2026-07-21T01:20:00+00:00"


def _sced(resource, when, *, base_point, consumption):
    return {
        "sced_time_utc": when.tz_convert("UTC"),
        "sced_time_cpt": when,
        "interval_start_utc": when.tz_convert("UTC").floor("5min"),
        "resource": resource,
        "qse": "QBASTX",
        "base_point_mw": base_point,
        "real_power_consumption_mw": consumption,
        "deviation_mw": consumption - base_point,
        "as_award_ecrs_mw": 0.0,
        "as_award_nspin_mw": 0.0,
    }


def _market_intervals(store, day, starts):
    index = pd.DatetimeIndex(starts, tz="UTC", name="interval_start_utc")
    intervals = pd.DataFrame(
        {
            "interval_start_cpt": index.tz_convert("America/Chicago"),
            "operating_day": pd.Timestamp(day),
            "cpt_dst": True,
            "repeated_hour": False,
        },
        index=index,
    )
    for field in FIELDS:
        intervals[field.name] = 10.0
        intervals[quality_column(field.name)] = "ok"
    MarketStore(store).write("intervals", day, intervals)


def covered_day(tmp_path):
    """Market intervals for the two known SCED runs, plus the Base-actual day."""
    store = tmp_path / "market"
    build_base_actual(DAY, DAY, raw_dir=RAW, store_dir=store, fetch=False, today=PUBLISHED_BY)
    _market_intervals(store, DAY, [HOUR_1, DISPATCH])
    return store


def test_harness_run_base_actual_reports_the_fixture_awards(tmp_path):
    out = tmp_path / "run"

    code = main(["run", "--policy", "base_actual", "--scenario", "baseline",
                 "--market-dir", str(covered_day(tmp_path)),
                 "--start", "2026-07-20", "--out", str(out), "--seed", "1"])

    assert code == 0
    dump = pd.read_parquet(out / "intervals.parquet")
    reported = dump[dump["fleet_case"] == "P50"].pivot(
        index="interval_start_utc", columns="product", values="reported_mw")
    assert reported.loc[pd.Timestamp(HOUR_1), "ECRS"] == pytest.approx(6.4)
    assert reported.loc[pd.Timestamp(HOUR_1), "NONSPIN"] == pytest.approx(19.2)
    assert reported.loc[pd.Timestamp(DISPATCH), "ECRS"] == pytest.approx(6.6)
    assert reported.loc[pd.Timestamp(DISPATCH), "NONSPIN"] == pytest.approx(0.0)
    cards = (out / "scorecard.json").read_text()
    assert '"policy": "base_actual"' in cards


def test_harness_run_base_actual_refuses_a_day_the_dataset_does_not_cover(tmp_path, capsys):
    code = main(["run", "--policy", "base_actual", "--scenario", "baseline",
                 "--market-dir", str(covered_day(tmp_path)),
                 "--start", "2026-07-20", "--end", "2026-07-21",
                 "--out", str(tmp_path / "run")])

    assert code == 1
    assert "2026-07-21" in capsys.readouterr().err


def test_base_actual_grades_delivery_on_the_known_dispatch(tmp_path):
    out = tmp_path / "run"

    code = main(["run", "--policy", "base_actual", "--scenario", "baseline",
                 "--market-dir", str(covered_day(tmp_path)),
                 "--start", "2026-07-20", "--out", str(out), "--seed", "1"])

    assert code == 0
    report = json.loads((out / "delivery.json").read_text())
    by_resource = {event["resource"]: event for event in report["events"]}

    # Means of the runs above; shortfall is the worst run's RPC − Base Point.
    ob = by_resource["OB_ALD1"]
    assert ob["requested_mw"] == pytest.approx(24.27044677734375)
    assert ob["delivered_mw"] == pytest.approx(48.85)
    assert ob["shortfall_mw"] == pytest.approx(24.989569091796902)
    assert ob["within_tolerance"] is False

    sans = by_resource["SANSM_ALD1"]
    assert sans["requested_mw"] == pytest.approx(24.0999755859375)
    assert sans["delivered_mw"] == pytest.approx(30.2)
    assert sans["shortfall_mw"] == pytest.approx(6.100024414062499)
    assert sans["within_tolerance"] is False

    # Both misses exceed the lesser of 3% of requested MW and 3 MW.
    summary = report["summary"]
    assert summary["events"] == 2
    assert summary["within_tolerance"] == 0
    assert summary["share_within_tolerance"] == 0.0
    assert summary["worst_shortfall_mw"] == pytest.approx(24.989569091796902)

    prose = (out / "report.md").read_text()
    assert "OB_ALD1" in prose and "SANSM_ALD1" in prose
    assert "Requested MW" in prose and "Delivered MW" in prose
    assert "Worst shortfall 24.990 MW" in prose


def test_an_event_inside_the_set_point_band_counts_as_delivered(tmp_path):
    # Two one-run events, both above the 2 MW dispatch threshold. The band is
    # the lesser of 3% of Base Point and 3 MW, so 2.5 MW on a 100 MW Base Point
    # is inside and 10 MW is not.
    day = dt.date(2026, 8, 1)
    when = pd.Timestamp("2026-08-01 12:00:00", tz="America/Chicago")
    later = when + pd.Timedelta(minutes=5)
    rows = pd.DataFrame([
        _sced("CLOSE_ALD1", when, base_point=100.0, consumption=102.5),
        _sced("MISS_ALD1", later, base_point=100.0, consumption=110.0),
    ])
    store = tmp_path / "market"
    MarketStore(store).write("base_actual", day, rows)
    _market_intervals(store, day, [when.tz_convert("UTC")])
    out = tmp_path / "run"

    code = main(["run", "--policy", "base_actual", "--scenario", "baseline",
                 "--market-dir", str(store), "--start", day.isoformat(), "--out", str(out)])

    assert code == 0
    report = json.loads((out / "delivery.json").read_text())
    by_resource = {event["resource"]: event for event in report["events"]}
    assert by_resource["CLOSE_ALD1"]["within_tolerance"] is True
    assert by_resource["CLOSE_ALD1"]["requested_mw"] == pytest.approx(100.0)
    assert by_resource["CLOSE_ALD1"]["delivered_mw"] == pytest.approx(102.5)
    assert by_resource["CLOSE_ALD1"]["shortfall_mw"] == pytest.approx(2.5)
    assert by_resource["MISS_ALD1"]["within_tolerance"] is False
    assert report["summary"]["within_tolerance"] == 1
    assert report["summary"]["share_within_tolerance"] == 0.5
    assert report["summary"]["worst_shortfall_mw"] == pytest.approx(10.0)


def test_compare_puts_base_actual_on_the_frontier_and_exceedance_chart(tmp_path):
    out = tmp_path / "compare"

    code = main(["compare", "--policy", "constant_haircut", "--policy", "base_actual",
                 "--scenario", "baseline", "--market-dir", str(covered_day(tmp_path)),
                 "--start", "2026-07-20", "--out", str(out), "--seed", "1"])

    assert code == 0
    # Settlement MCPC is $10/MW-h on both fixture intervals. Base's awards are
    # 6.4 and 6.6 MW ECRS and 19.2 and 0 MW Non-Spin, so revenue is
    # (6.4 + 6.6 + 19.2) * 10 * 5/60 = $26.8333 on every fleet case.
    points: dict[str, float] = {}
    for el in ET.fromstring((out / "frontier.svg").read_text()).iter():
        name = el.get("data-policy")
        revenue = el.get("data-revenue")
        if name is not None and revenue is not None:
            points[name] = float(revenue)
    assert points["base_actual"] == pytest.approx(26.833333333333332)
    assert any(name.startswith("constant_haircut") for name in points)

    exceedance = (out / "exceedance.md").read_text()
    assert "base_actual" in exceedance
    assert "constant_haircut" in exceedance
    assert "P(hour >= x)" in exceedance
