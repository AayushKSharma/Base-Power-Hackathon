"""Forecasts that drive a capacity algorithm, and the value of that forecast.

The fixture is two hours on 2026-03-09. Every price in the first hour is $5/MW-h
and every price in the second hour is $20/MW-h. Load-zone prices are $0. A
forecaster is asked once per hour, from data strictly before that hour, so
persistence has nothing at midnight and then repeats $5. The oracle reports the
hour that is about to happen.
"""

import datetime as dt
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from harness.cli import main
from harness.market.catalog import CPT, Q_OK, quality_column
from harness.market.normalize import day_grid
from harness.market.store import MarketStore

DAY = dt.date(2026, 3, 9)
RECORDER = Path(__file__).parent / "policies" / "recorder_policy.py"
# First step of the median. The hour of $20 has not started, so persistence still says $5.
HOUR_1 = "2026-03-09T05:00:00+00:00"
HOUR_2 = "2026-03-09T06:00:00+00:00"


def test_harness_run_gives_the_policy_the_forecasters_output(tmp_path):
    recorded = tmp_path / "seen.jsonl"
    out = tmp_path / "run"
    command = f"{sys.executable} {RECORDER} --out {recorded}"

    code = main([
        "run", "--policy", command, "--scenario", str(_scenario(tmp_path)),
        "--start", DAY.isoformat(), "--market-dir", str(_market(tmp_path)),
        "--forecaster", "persistence", "--out", str(out), "--seed", "1",
    ])

    assert code == 0
    seen = [json.loads(line) for line in recorded.read_text().splitlines()]
    at_midnight = next(obs for obs in seen if obs["now"]["interval_start_utc"] == HOUR_1)
    at_one = next(obs for obs in seen if obs["now"]["interval_start_utc"] == HOUR_2)
    # The current row at 01:00 is already $20. Persistence, issued at the hour,
    # can only repeat the previous hour's $5.
    assert at_one["now"]["rt_mcpc"]["ECRS"] == 20.0
    assert _median(at_midnight, "MCPC_ECRS") == 0.0
    assert _median(at_one, "MCPC_ECRS") == 5.0
    assert _median(at_one, "LZ_HOUSTON") == 0.0


def test_compare_scores_with_the_forecaster_and_reports_its_value(tmp_path):
    # Persistence misses the $5 hour (it has no earlier price, so the newsvendor
    # reports 0 MW) and then sells the whole fleet through the $20 hour.
    # Revenue is 0.032 MW * $20/MW-h * 1 h * 2 products = $1.28.
    # The oracle sells through both hours: that $1.28, plus 0.032 MW * $5/MW-h
    # * 1 h * 2 products = $0.32, so $1.60. The spot price, without a forecaster,
    # matches the oracle. The lower frontier revenue is the forecast being used.
    out = tmp_path / "compare"

    code = main([
        "compare", "--policy", "independent_newsvendor", "--forecaster", "persistence",
        "--scenario", str(_scenario(tmp_path)), "--start", DAY.isoformat(),
        "--market-dir", str(_market(tmp_path)), "--out", str(out), "--seed", "1",
    ])

    assert code == 0
    assert _frontier(out)["independent_newsvendor"] == pytest.approx(1.28)
    section = (out / "exceedance.md").read_text().split("Value of the forecast", 1)[1]
    assert "Versus oracle" in section
    assert "Versus persistence" in section
    assert "1.28" in section
    assert "1.60" in section
    assert "-0.32" in section
    assert "ECRS" in section


def test_harness_value_reports_deltas_and_places_the_oracle_ahead_of_persistence(tmp_path, capsys):
    # Same hours as the comparison. Persistence earns $1.28 and leaves 0.064 MW-h
    # under-sold. The oracle earns $1.60 and under-sells nothing. Nothing is
    # over-sold, and nothing is deployed, so every exceedance probability matches.
    out = tmp_path / "value"

    code = main([
        "value", "--policy", "independent_newsvendor", "--forecaster", "persistence",
        "--scenario", str(_scenario(tmp_path)), "--start", DAY.isoformat(),
        "--market-dir", str(_market(tmp_path)), "--out", str(out), "--seed", "1",
    ])

    assert code == 0
    printed = capsys.readouterr().out
    assert "Versus oracle" in printed
    assert "Versus persistence" in printed
    saved = json.loads((out / "value.json").read_text())
    assert saved["oracle"]["revenue"] == pytest.approx(1.60)
    assert saved["persistence"]["revenue"] == pytest.approx(1.28)
    assert saved["oracle"]["undersold_mw_h"] == pytest.approx(0.0)
    assert saved["persistence"]["undersold_mw_h"] == pytest.approx(0.064)
    assert saved["oracle"]["oversold_mw_h"] == pytest.approx(0.0)
    assert saved["persistence"]["oversold_mw_h"] == pytest.approx(0.0)
    assert saved["oracle"]["revenue"] >= saved["persistence"]["revenue"]
    assert saved["oracle"]["undersold_mw_h"] <= saved["persistence"]["undersold_mw_h"]
    assert saved["vs_oracle"]["revenue"] == pytest.approx(-0.32)
    assert saved["vs_oracle"]["undersold_mw_h"] == pytest.approx(0.064)
    assert saved["vs_persistence"]["revenue"] == pytest.approx(0.0)
    assert saved["vs_oracle"]["exceedance"]["ECRS"] == [
        {"mw": 0.0, "delta": pytest.approx(0.0)},
        {"mw": 1.0, "delta": pytest.approx(0.0)},
    ]
    chart = _chart_points(out / "value.svg")
    assert chart["oracle"]["revenue"] == pytest.approx(1.60)
    assert chart["persistence"]["revenue"] == pytest.approx(1.28)
    assert chart["chosen"]["revenue"] == pytest.approx(1.28)
    ends = sorted((chart["persistence"]["x"], chart["oracle"]["x"]))
    assert ends[0] <= chart["chosen"]["x"] <= ends[1]


def _chart_points(path: Path) -> dict[str, dict[str, float]]:
    points = {}
    for el in ET.fromstring(path.read_text()).iter():
        arm = el.get("data-arm")
        if arm is None or el.get("cx") is None:
            continue
        points[arm] = {"x": float(el.get("cx") or 0), "revenue": float(el.get("data-revenue") or 0)}
    return points


def _frontier(out: Path) -> dict[str, float]:
    points = {}
    for el in ET.fromstring((out / "frontier.svg").read_text()).iter():
        name = el.get("data-policy")
        revenue = el.get("data-revenue")
        if name is not None and revenue is not None:
            points[name] = float(revenue)
    return points


def _median(observation: dict, series: str) -> float:
    spec = observation["forecaster"]["series"][series]
    quantiles = spec["quantiles"]
    index = min(range(len(quantiles)), key=lambda i: abs(quantiles[i] - 0.5))
    return spec["values"][index][0]


def _scenario(tmp_path: Path) -> Path:
    path = tmp_path / "predictable.yaml"
    path.write_text("""\
seed: 1
fleet:
  homes: 4
  regions: 4
  battery_kwh: 20
  inverter_kw: 10
  backup_floor: 0.2
  soc: {fixed: 0.6}
  telemetry_stale_s: 0
  state: stochastic
  quantile_mock:
    shares: {P10: 1.0, P25: 1.0, P50: 1.0, P75: 1.0, P90: 1.0}
    policy_view: per_case
    typical: P50
failures:
  home_dropout_per_h: 0.0
  home_dropout_min: 60
  region_outage_per_h: 0.0
  region_outage_min: 120
  scarcity_stress: 1.0
  forced_region_outages: []
deployments:
  calm: 0.0
  scarce: 0.0
  refill_kw: 0.0
  forced: []
scoring:
  preset: energy
  load_zone: HOUSTON
  compliance_per_mw: 0.0
  spd_per_mwh: 0.0
  exceedance_mw: [0.0, 1.0]
  tolerance_mw: [1.0]
products:
  ECRS: {duration_h: 1.0, cap_mw: 100.0, cap_share: 0.9}
  NONSPIN: {duration_h: 1.0, cap_mw: 100.0, cap_share: 0.9}
""")
    return path


def _market(tmp_path: Path) -> Path:
    """Two hours: $5, then $20, on every RT MCPC. Load-zone prices stay at $0."""
    root = tmp_path / "market"
    grid = day_grid(DAY)[:24]
    hour = grid.tz_convert(CPT).hour.to_numpy()
    price = np.where(hour == 0, 5.0, 20.0)
    frame = pd.DataFrame({
        "interval_start_cpt": grid.tz_convert(CPT),
        "operating_day": pd.Timestamp(DAY),
        "rt_mcpc_5m_regup": price,
        "rt_mcpc_5m_regdn": price,
        "rt_mcpc_5m_rrs": price,
        "rt_mcpc_5m_ecrs": price,
        "rt_mcpc_5m_nspin": price,
        "rt_mcpc_15m_ecrs": price,
        "rt_mcpc_15m_nspin": price,
        "lz_spp_houston": 0.0,
        "lz_spp_north": 0.0,
        "lz_spp_south": 0.0,
        "lz_spp_west": 0.0,
    }, index=grid)
    for column in ("rt_mcpc_15m_ecrs", "rt_mcpc_15m_nspin"):
        frame[quality_column(column)] = Q_OK
    MarketStore(root).write("intervals", DAY, frame)
    return root
