from textwrap import dedent

import numpy as np
import pandas as pd
import pytest

from conftest import SCENARIOS
from harness import ScenarioError, load_scenario

VALID = """\
seed: 7
fleet:
  homes: 100
  regions: 4
  battery_kwh: 20
  inverter_kw: 10
  backup_floor: 0.2
  soc: {fixed: 0.6}
  telemetry_stale_s: 180
  state: quantile
  quantile_mock: {P10: 0.8, P25: 0.85, P50: 0.9, P75: 0.95, P90: 1.0}
products:
  ECRS: {duration_h: 1, cap_mw: 100, cap_share: 0.9}
  NONSPIN: {duration_h: 4, cap_mw: 100, cap_share: 0.9}
"""

VALID += """\
failures:
  home_dropout_per_h: 0.01
  home_dropout_min: 60
  region_outage_per_h: 0.005
  region_outage_min: 120
  scarcity_stress: 4
  forced_region_outages: [{region: 3, start: "2026-08-26 18:00", minutes: 240}]
"""


def write(tmp_path, text, name="custom.yaml"):
    path = tmp_path / name
    path.write_text(dedent(text))
    return path


@pytest.mark.parametrize("name", ["baseline", "stochastic"])
def test_the_shipped_scenarios_load(name):
    sc = load_scenario(SCENARIOS / f"{name}.yaml")

    assert sc.name == name
    assert (sc.fleet.homes, sc.fleet.battery_kwh, sc.fleet.inverter_kw) == (1000, 39.2, 20.0)
    assert sc.products["ECRS"].award_limit_mw == pytest.approx(90.0)


def test_a_scenario_is_named_after_its_file(tmp_path):
    sc = load_scenario(write(tmp_path, VALID, name="calm_week.yaml"))

    assert (sc.name, sc.seed, sc.fleet.homes) == ("calm_week", 7, 100)


def test_one_line_switches_a_scenario_to_the_failure_model(tmp_path):
    sc = load_scenario(write(tmp_path, VALID.replace("state: quantile", "state: stochastic")))

    assert sc.fleet.state == "stochastic"
    (outage,) = sc.failures.forced_region_outages
    assert outage.region == 3
    assert outage.start == pd.Timestamp("2026-08-26 23:00", tz="UTC")  # 18:00 CDT
    assert outage.minutes == 240


WITH_MOCK_CSV = VALID.replace("{P10: 0.8, P25: 0.85, P50: 0.9, P75: 0.95, P90: 1.0}", "mock.csv")


def write_mock(tmp_path, shares):
    table = pd.DataFrame([(m, h) for m in range(1, 13) for h in range(24)], columns=["month", "hour"])
    for i, q in enumerate(["P10", "P25", "P50", "P75", "P90"]):
        table[q] = shares(i)
    table.to_csv(tmp_path / "mock.csv", index=False)


def test_a_quantile_mock_table_is_read_relative_to_the_scenario(tmp_path):
    write_mock(tmp_path, lambda i: 0.5 + 0.1 * i)

    sc = load_scenario(write(tmp_path, WITH_MOCK_CSV))

    assert sc.fleet.quantile_mock.share("P90", np.array([3]), np.array([17])) == pytest.approx([0.9])


@pytest.mark.parametrize("csv, error", [
    ("", "cannot read"),
    ("month,hour,P10\n1,0,0.5\n", "expected columns month,hour,P10,P25,P50,P75,P90"),
    ("month,hour,P10,P25,P50,P75,P90\n1,0,.5,.6,.7,.8,.9\n", "one row per month 1-12 and hour 0-23"),
])
def test_a_malformed_quantile_mock_table_gives_a_clear_error(tmp_path, csv, error):
    (tmp_path / "mock.csv").write_text(csv)

    with pytest.raises(ScenarioError, match=error):
        load_scenario(write(tmp_path, WITH_MOCK_CSV))


def test_quantile_mock_shares_must_be_numbers(tmp_path):
    write_mock(tmp_path, lambda i: "lots")

    with pytest.raises(ScenarioError, match="shares must be numbers between 0 and 1"):
        load_scenario(write(tmp_path, WITH_MOCK_CSV))


@pytest.mark.parametrize("text, error", [
    (VALID.replace("  homes: 100", "  homes: 100\n  nominal_mw: 80"),
     "fleet: unknown field 'nominal_mw'"),
    (VALID.replace("seed: 7\n", "seed: 7\nweather: {}\n"),
     "unknown field 'weather'"),
    (VALID.replace("{duration_h: 1, cap_mw: 100, cap_share: 0.9}\n", "{duration_h: 1, cap_mw: 100}\n", 1),
     "products.ECRS: missing field 'cap_share'"),
    (VALID.replace("seed: 7\n", ""),
     "missing field 'seed'"),
    (VALID.replace("  NONSPIN:", "  RRS:"),
     "products: unknown field 'RRS'"),
    (VALID.replace("battery_kwh: 20", "battery_kwh: lots"),
     "fleet.battery_kwh: expected a number, got 'lots'"),
    (VALID.replace("battery_kwh: 20", "battery_kwh: .nan"),
     "fleet.battery_kwh: expected a number, got nan"),
    (VALID.replace("seed: 7", "seed: 7.5"),
     "seed: expected a non-negative integer, got 7.5"),
    (VALID.replace("cap_share: 0.9}", "cap_share: 1.5}", 1),
     "products.ECRS.cap_share: must be between 0 and 1, got 1.5"),
    (VALID.replace("duration_h: 1,", "duration_h: 0,"),
     "products.ECRS.duration_h: must be positive"),
    (VALID.replace("homes: 100", "homes: 0"),
     "fleet.homes: expected an integer of at least 1, got 0"),
    (VALID.replace("regions: 4", "regions: 400"),
     "fleet.regions: cannot exceed homes (100), got 400"),
    (VALID.replace("state: quantile", "state: chaotic"),
     "fleet.state: expected quantile or stochastic, got 'chaotic'"),
    (VALID.replace("soc: {fixed: 0.6}", "soc: {mean: 0.6}"),
     "fleet.soc: expected {fixed: <SOC 0-1>} or {beta: [a, b]}"),
    (VALID.replace("soc: {fixed: 0.6}", "soc: {beta: [6]}"),
     "fleet.soc.beta: expected [a, b], got [6]"),
    (VALID.replace("P25: 0.85", "P25: 0.75"),
     "fleet.quantile_mock: shares must not fall from P10 to P90 (month 1, hour 0)"),
    (VALID.replace("P50: 0.9, ", ""),
     "fleet.quantile_mock: missing field 'P50'"),
    (VALID.replace("{P10: 0.8, P25: 0.85, P50: 0.9, P75: 0.95, P90: 1.0}", "nowhere.csv"),
     "fleet.quantile_mock ("),
    (VALID.split("failures:")[0],
     "missing field 'failures'"),
    (VALID.replace("region: 3", "region: 4"),
     "failures.forced_region_outages[0].region: must be below fleet.regions (4), got 4"),
    (VALID.replace('"2026-08-26 18:00"', '"soon"'),
     "failures.forced_region_outages[0].start: expected a CPT date and time"),
    # 01:30 happens twice on the fall-back day, and 02:30 never on the spring-forward day.
    (VALID.replace('"2026-08-26 18:00"', '"2026-11-01 01:30"'),
     "failures.forced_region_outages[0].start: expected a CPT date and time"),
    (VALID.replace('"2026-08-26 18:00"', '"2026-03-08 02:30"'),
     "failures.forced_region_outages[0].start: expected a CPT date and time"),
    ("- just\n- a list\n",
     "expected a mapping"),
    (VALID + "seed: 8\n",
     "duplicate field 'seed'"),
])
def test_invalid_scenarios_give_a_clear_error(tmp_path, text, error):
    path = write(tmp_path, text)

    with pytest.raises(ScenarioError) as raised:
        load_scenario(path)

    assert error in str(raised.value)
    assert str(path) in str(raised.value)


def test_a_missing_scenario_file_gives_a_clear_error(tmp_path):
    with pytest.raises(ScenarioError, match="nowhere.yaml: cannot read"):
        load_scenario(tmp_path / "nowhere.yaml")
