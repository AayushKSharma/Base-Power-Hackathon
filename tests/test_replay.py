"""Live replay: a coordinator and one agent-host per region, on a 2-second clock."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import CALM, SPRING_FORWARD, fleet_scenario, recorded
from harness.cli import main
from harness.external import ExternalPolicy
from harness.policy import ConstantHaircut
from harness.replay import HostKill, TransportFaults, multiprocessing_available, replay

# fleet_scenario: 20 kWh, 10 kW inverter, 20% floor, SOC 0.6.
# Each home has 8 kWh above the floor, so 8 kW of ECRS and 2 kW of Non-Spin.
# Two homes, both online: 16 kW and 4 kW, which is 0.016 MW and 0.004 MW.


def test_a_short_replay_writes_reported_capability_and_true_deliverable(market_store, tmp_path):
    out = tmp_path / "timeline.json"
    scenario = fleet_scenario(homes=2, regions=2)

    result = replay(
        ConstantHaircut(1.0),
        scenario,
        SPRING_FORWARD,
        seed=7,
        minutes=2,
        market=recorded(market_store),
        out=out,
    )

    assert [tick.t_s for tick in result.ticks] == [i * 2.0 for i in range(60)]
    for tick in result.ticks:
        assert tick.reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
        assert tick.deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})

    written = json.loads(out.read_text())
    assert (written["day"], written["seed"], written["tick_s"]) == ("2026-03-08", 7, 2.0)
    assert written["ticks"][0]["reported_mw"]["ECRS"] == pytest.approx(0.016)
    assert written["ticks"][0]["deliverable_mw"]["NONSPIN"] == pytest.approx(0.004)
    assert len(written["ticks"]) == len(result.ticks)


def test_killing_an_agent_host_drops_reported_capability_once_telemetry_is_stale(market_store):
    """The host for region 0 is killed at 2 s, after its heartbeat at 0 s.

    Telemetry is excluded only once it is older than 180 s, so the next 2-second
    tick (182 s) is the first that drops that region's 0.008 MW. The homes are
    still there, so true deliverable MW does not move.
    """
    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=4,
        market=recorded(market_store),
        kills=(HostKill(region=0, at_s=2.0),),
    )

    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[180.0].reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    assert by_time[182.0].reported_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})
    assert by_time[182.0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_telemetry_older_than_180s_is_excluded_from_capability(market_store):
    """A delayed reading keeps the time it was sent.

    At a 182 s delay the first heartbeat arrives older than 180 s, so it adds
    nothing. At a 180 s delay that same heartbeat is still in date.
    """
    scenario = fleet_scenario(homes=2, regions=2)
    stale = replay(
        ConstantHaircut(1.0), scenario, SPRING_FORWARD, seed=7, minutes=184 / 60,
        market=recorded(market_store), transport=TransportFaults(delay_s=182),
    )
    held = replay(
        ConstantHaircut(1.0), scenario, SPRING_FORWARD, seed=7, minutes=184 / 60,
        market=recorded(market_store), transport=TransportFaults(delay_s=180),
    )

    arrived = [tick for tick in stale.ticks if tick.t_s >= 182]
    assert [tick.t_s for tick in arrived] == [182.0]
    assert arrived[0].reported_mw == pytest.approx({"ECRS": 0.0, "NONSPIN": 0.0})
    assert arrived[0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    fresh = next(tick for tick in held.ticks if tick.t_s == 180.0)
    assert fresh.reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_a_partition_and_a_dropped_heartbeat_leave_those_homes_unreported(market_store):
    """Region 0 is partitioned for the whole replay. Region 1's heartbeat at 0 s is dropped.

    Nothing is reported at 0 s. From 2 s on, only region 1's home counts: 0.008 MW
    of ECRS. True deliverable MW still includes both homes.
    """
    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        transport=TransportFaults(partition_regions=frozenset({0}), drop=((1, 0.0),)),
    )

    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[0.0].reported_mw == pytest.approx({"ECRS": 0.0, "NONSPIN": 0.0})
    assert by_time[2.0].reported_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})
    assert by_time[2.0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_the_same_seed_gives_the_same_timeline(market_store):
    scenario = fleet_scenario(homes=4, regions=2, state="stochastic", soc={"beta": [6, 4]}, failures=CALM)

    def once(seed: int):
        return replay(ConstantHaircut(1.0), scenario, SPRING_FORWARD, seed=seed, minutes=1,
                      market=recorded(market_store))

    first = once(1)
    assert first.ticks == once(1).ticks
    assert first.ticks != once(2).ticks


def require_multiprocessing() -> None:
    if not multiprocessing_available():
        pytest.skip("multiprocessing is not available")


@pytest.fixture(autouse=True)
def _replay_processes():
    require_multiprocessing()


def test_replay_skips_when_multiprocessing_is_unavailable(monkeypatch):
    monkeypatch.setattr(f"{__name__}.multiprocessing_available", lambda: False)
    with pytest.raises(pytest.skip.Exception):
        require_multiprocessing()


def test_harness_replay_writes_a_timeline(market_store, tmp_path):
    scenario = tmp_path / "two_homes.yaml"
    scenario.write_text(_TWO_HOMES)
    out = tmp_path / "replay"

    code = main([
        "replay", "--policy", "constant_haircut", "--param", "fraction=1",
        "--scenario", str(scenario), "--day", "2026-03-08", "--minutes", "1",
        "--seed", "7", "--market-dir", str(market_store), "--out", str(out),
    ])

    assert code == 0
    written = json.loads((out / "timeline.json").read_text())
    assert (written["day"], written["seed"], written["tick_s"]) == ("2026-03-08", 7, 2.0)
    assert len(written["ticks"]) == 30
    assert written["ticks"][0]["reported_mw"] == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    assert written["ticks"][0]["deliverable_mw"] == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_an_external_policy_speaks_the_same_protocol(market_store):
    scenario = fleet_scenario(homes=2, regions=2)
    command = [sys.executable, str(EXAMPLE), "--fraction", "1"]

    result = replay(
        ExternalPolicy(command, scenario.products),
        scenario,
        SPRING_FORWARD,
        seed=7,
        minutes=2 / 60,
        market=recorded(market_store),
    )

    assert result.ticks[0].reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "constant_haircut_policy.py"

_TWO_HOMES = """\
seed: 7
fleet:
  homes: 2
  regions: 2
  battery_kwh: 20
  inverter_kw: 10
  backup_floor: 0.2
  soc: {fixed: 0.6}
  telemetry_stale_s: 180
  state: quantile
  quantile_mock:
    shares: {P10: 1, P25: 1, P50: 1, P75: 1, P90: 1}
    policy_view: typical
    typical: P50
failures:
  home_dropout_per_h: 0
  home_dropout_min: 60
  region_outage_per_h: 0
  region_outage_min: 120
  scarcity_stress: 1
  forced_region_outages: []
deployments: {calm: 0, scarce: 0, refill_kw: 0, forced: []}
scoring:
  preset: energy
  load_zone: HOUSTON
  compliance_per_mw: 0
  spd_per_mwh: 0
  exceedance_mw: [0, 1]
  tolerance_mw: [1]
products:
  ECRS: {duration_h: 1, cap_mw: 100, cap_share: 0.9}
  NONSPIN: {duration_h: 4, cap_mw: 100, cap_share: 0.9}
"""
