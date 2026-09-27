"""Calibrated fleet-state quantile mocks from a Base-actual fixture.

The fixture's QBASTX rows are two ALRs whose headroom (MPC − LPC) sums to 100 MW
each SCED run. Flexible MW (RPC − LPC) sums to 20, 40, 50, 70 and 90 in hour 14
and to 10 and 50 in hour 3, so the per-MW-of-fleet shares are
0.20, 0.40, 0.50, 0.70, 0.90 and 0.10, 0.50. A third QSE's ALR is on the same
runs with a much larger flexible MW and must not enter the profile.

Linear quantile positions on the five hour-14 shares (n = 5, index (n−1)·q):
P10 = 0.28, P25 = 0.40, P50 = 0.50, P75 = 0.70, P90 = 0.82.
On the two hour-3 shares (n = 2):
P10 = 0.14, P25 = 0.20, P50 = 0.30, P75 = 0.40, P90 = 0.46.
August 1 hour 14 adds 0.10, 0.30, 0.60 and 0.80, which changes the pooled hour
and the all-run fallback, not July's own buckets.
"""

import datetime as dt
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import SPRING_FORWARD, FixedPolicy, fleet_scenario, recorded
from harness import load_scenario, run

from harness.calibration import main
from harness.market.catalog import CPT
from harness.market.store import MarketStore

FIXTURE = Path(__file__).parent / "fixtures" / "calibration" / "base_actual_sced.csv"
START = "2026-07-31"
END = "2026-08-01"
QUANTILES = ["P10", "P25", "P50", "P75", "P90"]


def base_actual_store(tmp_path):
    raw = pd.read_csv(FIXTURE)
    cpt = pd.to_datetime(raw["sced_time_cpt"]).dt.tz_localize(CPT)
    rows = pd.DataFrame({
        "sced_time_utc": cpt.dt.tz_convert("UTC"),
        "sced_time_cpt": cpt,
        "qse": raw["qse"],
        "resource": raw["resource"],
        "low_power_consumption_mw": raw["low_power_consumption_mw"],
        "real_power_consumption_mw": raw["real_power_consumption_mw"],
        "max_power_consumption_mw": raw["max_power_consumption_mw"],
    })
    store = tmp_path / "market"
    market = MarketStore(store)
    for day, group in rows.groupby(rows["sced_time_cpt"].dt.date, sort=True):
        market.write("base_actual", day, group.reset_index(drop=True))
    return store


def calibrate(tmp_path):
    out = tmp_path / "base-actual"
    code = main(["--store-dir", str(base_actual_store(tmp_path)),
                 "--start", START, "--end", END, "--out", str(out)])
    assert code == 0
    return out


def shares(out):
    table = pd.read_csv(out / "shares.csv")
    assert list(table.columns) == ["month", "hour", *QUANTILES]
    return table.set_index(["month", "hour"])


def test_calibration_writes_the_fixture_quantiles_for_buckets_with_data(tmp_path):
    table = shares(calibrate(tmp_path))

    # July (month 7), hour 14 and hour 3: the worked examples above.
    assert table.loc[(7, 14), QUANTILES].tolist() == pytest.approx([0.28, 0.40, 0.50, 0.70, 0.82])
    assert table.loc[(7, 3), QUANTILES].tolist() == pytest.approx([0.14, 0.20, 0.30, 0.40, 0.46])
    filled = table.loc[[(7, 14), (7, 3)]]
    assert (filled.diff(axis=1).iloc[:, 1:].ge(0).all()).all()


def test_empty_buckets_fall_back_and_stay_monotone(tmp_path):
    # Hour 14 across both days, sorted: 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90.
    # All eleven shares, sorted: 0.10, 0.10, 0.20, 0.30, 0.40, 0.50, 0.50, 0.60, 0.70, 0.80, 0.90.
    out = calibrate(tmp_path)
    table = shares(out)
    flagged = {(b["month"], b["hour"]): b["fallback"]
               for b in json.loads((out / "provenance.json").read_text())["fallback_buckets"]}

    assert table.loc[(1, 14), QUANTILES].tolist() == pytest.approx([0.18, 0.30, 0.50, 0.70, 0.82])
    assert flagged[(1, 14)] == "hour"
    assert table.loc[(1, 0), QUANTILES].tolist() == pytest.approx([0.10, 0.25, 0.50, 0.65, 0.80])
    assert flagged[(1, 0)] == "all"
    assert (7, 14) not in flagged
    assert (8, 14) not in flagged
    values = table[QUANTILES].to_numpy()
    assert (values[:, 1:] >= values[:, :-1]).all()


def test_calibration_records_the_source_range_and_resources(tmp_path):
    provenance = json.loads((calibrate(tmp_path) / "provenance.json").read_text())

    assert provenance["start"] == START
    assert provenance["end"] == END
    assert provenance["qse"] == "QBASTX"
    assert provenance["resources"] == ["OB_ALD1", "SANSM_ALD1"]


def test_a_scenario_pointing_at_the_calibrated_shares_is_scored_by_run(tmp_path, market_store):
    # March has no samples. Hour 3 falls back to July's two shares (0.10 and 0.50):
    # P10 0.14, P50 0.30, P90 0.46 of 100 homes, 8 kW of ECRS each.
    shares_csv = calibrate(tmp_path) / "shares.csv"
    result = run(FixedPolicy(0, 0), fleet_scenario(shares=str(shares_csv), homes=100),
                 SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    delivered = rows_at(result, "2026-03-08T03:00").set_index("fleet_case")["deliverable_mw"]
    assert delivered["P10"] == pytest.approx(0.112)
    assert delivered["P50"] == pytest.approx(0.24)
    assert delivered["P90"] == pytest.approx(0.368)


def test_the_command_writes_a_scenario_that_selects_the_calibrated_shares(tmp_path):
    out = calibrate(tmp_path)

    sc = load_scenario(out / "scenario.yaml")

    assert sc.fleet.state == "quantile"
    assert sc.fleet.quantile_mock.share("P10", np.array([7]), np.array([14])) == pytest.approx([0.28])
    assert sc.fleet.quantile_mock.share("P90", np.array([7]), np.array([14])) == pytest.approx([0.82])


def test_the_chart_draws_calibrated_bands_by_hour_of_day(tmp_path):
    # Hour 14 pools July and August. Hour 3 is July only. Hour 0 has no runs,
    # so it is not drawn as the all-run distribution.
    root = ET.fromstring((calibrate(tmp_path) / "bands.svg").read_text())
    left, right, top, bottom = (float(root.attrib[name]) for name in
                                ("data-plot-left", "data-plot-right", "data-plot-top", "data-plot-bottom"))
    band = root.find(".//{http://www.w3.org/2000/svg}g[@id='band']")
    assert band is not None
    lines = band.findall("{http://www.w3.org/2000/svg}line")

    def y(share):
        return top + (1 - share) * (bottom - top)

    def at(hour):
        x = left + hour * (right - left) / 23
        found: list[float] = []
        for line in lines:
            if abs(float(line.attrib["x1"]) - x) < 1e-6 and abs(float(line.attrib["x2"]) - x) < 1e-6:
                found.extend((float(line.attrib["y1"]), float(line.attrib["y2"])))
        return sorted(found)

    assert at(14) == pytest.approx(sorted((y(0.82), y(0.18))))
    assert at(3) == pytest.approx(sorted((y(0.46), y(0.14))))
    assert at(0) == []


def rows_at(result, cpt, product="ECRS"):
    dump = result.intervals
    local = dump["interval_start_cpt"].dt.strftime("%Y-%m-%dT%H:%M")
    return dump[(local == cpt) & (dump["product"] == product)]


def test_a_share_past_the_unit_interval_is_clipped_so_the_scenario_loads(tmp_path):
    # Two runs in one hour, ratios 0.50 and 1.50. Linear quantiles are
    # 0.60, 0.75, 1.00, 1.25, 1.40; the CSV keeps the ones above 1 at 1.
    cpt = pd.Timestamp("2026-07-31 14:05:20", tz=CPT)
    later = cpt + pd.Timedelta(minutes=5)
    rows = pd.DataFrame({
        "sced_time_utc": [cpt.tz_convert("UTC"), later.tz_convert("UTC")],
        "sced_time_cpt": [cpt, later],
        "qse": ["QBASTX", "QBASTX"],
        "resource": ["OB_ALD1", "OB_ALD1"],
        "low_power_consumption_mw": [0.0, 0.0],
        "real_power_consumption_mw": [50.0, 150.0],
        "max_power_consumption_mw": [100.0, 100.0],
    })
    store = tmp_path / "market"
    MarketStore(store).write("base_actual", dt.date(2026, 7, 31), rows)
    out = tmp_path / "clipped"
    assert main(["--store-dir", str(store), "--start", "2026-07-31", "--end", "2026-07-31",
                 "--out", str(out)]) == 0

    sc = load_scenario(out / "scenario.yaml")
    got = [sc.fleet.quantile_mock.share(q, np.array([7]), np.array([14])).item() for q in QUANTILES]
    assert got == pytest.approx([0.60, 0.75, 1.00, 1.00, 1.00])
    assert json.loads((out / "provenance.json").read_text())["clipped_runs"] == 1


def test_a_run_with_no_headroom_is_left_out_and_counted(tmp_path):
    # Headroom 0 cannot be a per-MW share. The other run, ratio 0.50, is the profile.
    cpt = pd.Timestamp("2026-07-31 14:05:20", tz=CPT)
    later = cpt + pd.Timedelta(minutes=5)
    rows = pd.DataFrame({
        "sced_time_utc": [cpt.tz_convert("UTC"), later.tz_convert("UTC")],
        "sced_time_cpt": [cpt, later],
        "qse": ["QBASTX", "QBASTX"],
        "resource": ["OB_ALD1", "OB_ALD1"],
        "low_power_consumption_mw": [0.0, 10.0],
        "real_power_consumption_mw": [50.0, 10.0],
        "max_power_consumption_mw": [100.0, 10.0],
    })
    store = tmp_path / "market"
    MarketStore(store).write("base_actual", dt.date(2026, 7, 31), rows)
    out = tmp_path / "skipped"
    assert main(["--store-dir", str(store), "--start", "2026-07-31", "--end", "2026-07-31",
                 "--out", str(out)]) == 0

    sc = load_scenario(out / "scenario.yaml")
    got = [sc.fleet.quantile_mock.share(q, np.array([7]), np.array([14])).item() for q in QUANTILES]
    assert got == pytest.approx([0.50, 0.50, 0.50, 0.50, 0.50])
    assert json.loads((out / "provenance.json").read_text())["skipped_runs"] == 1
