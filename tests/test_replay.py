"""Live replay: a coordinator and one agent-host per region, on a 2-second clock."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from conftest import CALM, NO_FAILURES, SPRING_FORWARD, fleet_scenario, recorded
from harness.cli import main
from harness.external import ExternalPolicy
from harness.farm import DEFAULT_DSN
from harness.policy import ConstantHaircut
from harness.replay import (
    CommandFaults,
    CoordinatorKill,
    HostKill,
    TransportFaults,
    load_chaos,
    multiprocessing_available,
    replay,
)

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


def test_harness_replay_chaos_writes_the_timeline_chart(market_store, tmp_path):
    scenario = tmp_path / "two_homes.yaml"
    scenario.write_text(_TWO_HOMES)
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: region_outage, at_s: 2, region: 0}\n")
    out = tmp_path / "replay"

    code = main([
        "replay", "--policy", "constant_haircut", "--param", "fraction=1",
        "--scenario", str(scenario), "--day", "2026-03-08", "--minutes", "1",
        "--seed", "7", "--market-dir", str(market_store), "--out", str(out),
        "--chaos", str(schedule),
    ])

    assert code == 0
    written = json.loads((out / "timeline.json").read_text())
    assert written["events"][0]["type"] == "region_outage"
    assert (out / "timeline.png").is_file()
    assert (out / "timeline.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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


def test_a_chaos_schedule_replays_identically_for_the_same_seed(market_store, tmp_path):
    """A 50% drop picks heartbeats from the seed. Stale telemetry is immediate, so
    the kept heartbeats are the timeline: the same seed matches, and another does not.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: drop, at_s: 0, percent: 50}\n")
    scenario = fleet_scenario(homes=2, regions=2, telemetry_stale_s=0)

    def once(seed: int):
        return replay(
            ConstantHaircut(1.0), scenario, SPRING_FORWARD, seed=seed, minutes=1,
            market=recorded(market_store), chaos=load_chaos(schedule),
        )

    first = once(1)
    assert first.ticks == once(1).ticks
    assert first.ticks != once(2).ticks
    assert any(tick.reported_mw["ECRS"] == pytest.approx(0.008) for tick in first.ticks)


def test_a_chaos_host_kill_drops_reported_capability_once_telemetry_is_stale(market_store, tmp_path):
    """The schedule kills region 0 at 2 s, after its heartbeat at 0 s.

    Telemetry is excluded only once it is older than 180 s, so 182 s is the first
    tick that drops that region's 0.008 MW. The homes are still there.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: host_kill, at_s: 2, region: 0}\n")

    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=4,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
    )

    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[180.0].reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    assert by_time[182.0].reported_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})
    assert by_time[182.0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_a_chaos_telemetry_delay_delivers_a_stale_heartbeat(market_store, tmp_path):
    """A 182 s delay from the first tick delivers that heartbeat already older than 180 s.

    It adds nothing. True deliverable MW still includes both homes.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: telemetry_delay, at_s: 0, delay_s: 182}\n")

    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=184 / 60,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
    )

    arrived = [tick for tick in result.ticks if tick.t_s >= 182]
    assert [tick.t_s for tick in arrived] == [182.0]
    assert arrived[0].reported_mw == pytest.approx({"ECRS": 0.0, "NONSPIN": 0.0})
    assert arrived[0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_a_chaos_partition_leaves_that_region_unreported(market_store, tmp_path):
    """Region 0 is partitioned from the first tick, so only region 1's 0.008 MW is reported."""
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: partition, at_s: 0, regions: [0]}\n")

    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
    )

    assert result.ticks
    for tick in result.ticks:
        assert tick.reported_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})
        assert tick.deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})


def test_a_chaos_region_outage_pushes_that_regions_homes_into_backup(market_store, tmp_path):
    """Region 0 loses the grid at 2 s, so its home goes into backup mode.

    From that tick, both reported capability and true deliverable MW are the
    other home only: 0.008 MW of ECRS and 0.002 MW of Non-Spin.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: region_outage, at_s: 2, region: 0}\n")

    result = replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
    )

    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[0.0].reported_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    assert by_time[0.0].deliverable_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.004})
    for t_s in (2.0, 58.0):
        tick = by_time[t_s]
        assert tick.reported_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})
        assert tick.deliverable_mw == pytest.approx({"ECRS": 0.008, "NONSPIN": 0.002})


def test_the_same_seed_gives_the_same_timeline(market_store):
    scenario = fleet_scenario(homes=4, regions=2, state="stochastic", soc={"beta": [6, 4]}, failures=CALM)

    def once(seed: int):
        return replay(ConstantHaircut(1.0), scenario, SPRING_FORWARD, seed=seed, minutes=1,
                      market=recorded(market_store))

    first = once(1)
    assert first.ticks == once(1).ticks
    assert first.ticks != once(2).ticks


COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def require_docker() -> None:
    if not docker_ready():
        pytest.skip("Docker is not available")


@pytest.fixture(scope="module")
def postgres_server():
    """The compose Postgres, reused when another checkout already owns the port."""
    require_docker()
    try:
        with psycopg.connect(DEFAULT_DSN) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--wait"],
            check=True,
        )
    deadline = time.monotonic() + 30
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(DEFAULT_DSN) as conn:
                conn.execute("SELECT 1")
            break
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(0.5)
    else:
        raise RuntimeError(f"Postgres did not accept connections: {last}")
    yield DEFAULT_DSN


@pytest.fixture
def replay_dsn(postgres_server):
    name = "replay_" + uuid.uuid4().hex
    with psycopg.connect(postgres_server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield postgres_server.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


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


def test_delivered_mw_tracks_commanded_mw_during_a_forced_deployment(market_store):
    """Four homes, haircut 0.5, ECRS forced on from midnight.

    Each home has 8 kW of ECRS, so the fleet has 0.032 MW and the policy reports
    0.016 MW. That award is the commanded MW for the whole minute, and the
    agents deliver it.
    """
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
    )

    assert len(result.ticks) == 30
    for tick in result.ticks:
        assert tick.commanded_mw == pytest.approx({"ECRS": 0.016, "NONSPIN": 0.0})
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)
        assert tick.delivered_mw["NONSPIN"] == pytest.approx(0.0)


def test_duplicated_and_reordered_commands_do_not_double_discharge(market_store):
    """Each command is delivered twice, and the previous tick's commands arrive after.

    The fleet still discharges the 0.016 MW award once. The double-discharge
    counter stays at zero.
    """
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        commands=CommandFaults(duplicate=True, reorder=True),
    )

    assert result.double_discharges == 0
    for tick in result.ticks:
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)
        assert tick.commanded_mw["ECRS"] == pytest.approx(0.016)


def test_the_backup_floor_holds_throughout_a_forced_deployment(market_store):
    """The discharge clamp never asks a home for more energy than it has above the floor."""
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
    )

    assert result.floor_breaches == 0


def test_backup_homes_are_not_allocated_during_a_forced_deployment(market_store):
    """Region 0 is in backup, so its homes add nothing.

    The other region's two homes have 16 kW of ECRS. The policy reports half
    of that, 0.008 MW, and that region delivers it.
    """
    outage = {**NO_FAILURES, "forced_region_outages": [
        {"region": 0, "start": "2026-03-08 00:00", "minutes": 60},
    ]}
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(state="stochastic", failures=outage, homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
    )

    assert result.floor_breaches == 0
    for tick in result.ticks:
        assert tick.commanded_mw["ECRS"] == pytest.approx(0.008)
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.008, abs=1e-6)


def test_killing_an_agent_host_mid_deployment_reassigns_its_share(market_store):
    """Region 0 is killed at 2 s, after both hosts have taken half of the award.

    Each region has 16 kW of ECRS, and the award is 0.016 MW, so the surviving
    host can carry all of it. Delivered MW is back on that target.
    """
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        kills=(HostKill(region=0, at_s=2.0),),
    )

    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[0.0].delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)
    for t_s in (2.0, 4.0, 58.0):
        tick = by_time[t_s]
        assert tick.commanded_mw["ECRS"] == pytest.approx(0.016)
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)


@pytest.mark.postgres
def test_a_coordinator_restart_resumes_the_deployment_without_double_commanding(market_store, replay_dsn):
    """The coordinator is killed at 2 s and a new process continues the same award.

    Versions live in Postgres, so the new commands are newer than the ones
    already applied, and the fleet discharges the 0.016 MW award once.
    """
    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        coordinator_kills=(CoordinatorKill(at_s=2.0),),
        dsn=replay_dsn,
    )

    assert result.double_discharges == 0
    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[0.0].delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)
    for t_s in (2.0, 4.0, 58.0):
        tick = by_time[t_s]
        assert tick.commanded_mw["ECRS"] == pytest.approx(0.016)
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)


@pytest.mark.postgres
def test_a_chaos_coordinator_restart_resumes_the_deployment_without_double_commanding(
        market_store, replay_dsn, tmp_path):
    """The schedule restarts the coordinator at 2 s. Versions live in Postgres,
    so the new commands are newer and the fleet still discharges 0.016 MW once.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: coordinator_restart, at_s: 2}\n")

    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=4, regions=2, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
        dsn=replay_dsn,
    )

    assert result.double_discharges == 0
    assert result.coordinator_restarts == 1
    by_time = {tick.t_s: tick for tick in result.ticks}
    assert by_time[0.0].delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)
    for t_s in (2.0, 4.0, 58.0):
        tick = by_time[t_s]
        assert tick.commanded_mw["ECRS"] == pytest.approx(0.016)
        assert tick.delivered_mw["ECRS"] == pytest.approx(0.016, abs=1e-6)


@pytest.mark.postgres
def test_the_full_chaos_schedule_reports_latency_and_keeps_the_zero_counters(
        market_store, replay_dsn, tmp_path):
    """Every failure type runs on one seeded schedule, and the safety counters stay zero.

    Five agents, haircut 0.5, so losing one (20%) still has the headroom to meet the
    award. Reaction samples, in seconds, are the partition (0), the outage (0), and
    the host kill, which the coordinator still counts until the 180 s stale rule
    (182 - 2 = 180). Sorted [0, 0, 180]: p50 is the middle value 0, and p99 is the
    linear point 98% of the way from 0 to 180, which is 176.4. Delivery is back on
    the award on the kill tick, so recovery p50 and p99 are 0.
    """
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("""\
events:
  - {type: partition, at_s: 0, regions: [4]}
  - {type: host_kill, at_s: 2, region: 0}
  - {type: telemetry_delay, at_s: 20, until_s: 24, delay_s: 2}
  - {type: region_outage, at_s: 40, region: 1}
  - {type: coordinator_restart, at_s: 190}
  - {type: drop, at_s: 200, percent: 100}
""")

    result = replay(
        ConstantHaircut(0.5),
        fleet_scenario(homes=5, regions=5, deployments=_FORCED_ECRS),
        SPRING_FORWARD,
        seed=7,
        minutes=4,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
        dsn=replay_dsn,
    )

    metrics = result.metrics
    assert metrics.reaction_p50_s == pytest.approx(0.0)
    assert metrics.reaction_p99_s == pytest.approx(176.4)
    assert metrics.recovery_p50_s == pytest.approx(0.0)
    assert metrics.recovery_p99_s == pytest.approx(0.0)
    assert metrics.floor_violations == 0
    assert metrics.duplicate_commands == 0
    assert metrics.dead_commands == 0
    assert result.coordinator_restarts == 1


def test_replay_writes_the_timeline_chart_with_chaos_events_marked(market_store, tmp_path):
    """The chart file is the four MW series for the demo, with each chaos event marked."""
    schedule = tmp_path / "chaos.yaml"
    schedule.write_text("events:\n  - {type: host_kill, at_s: 2, region: 0}\n")
    out = tmp_path / "timeline.json"
    chart = tmp_path / "timeline.png"

    replay(
        ConstantHaircut(1.0),
        fleet_scenario(homes=2, regions=2),
        SPRING_FORWARD,
        seed=7,
        minutes=1,
        market=recorded(market_store),
        chaos=load_chaos(schedule),
        out=out,
        chart=chart,
    )

    written = json.loads(out.read_text())
    assert written["events"] == [{"type": "host_kill", "at_s": 2.0, "region": 0}]
    assert set(written["ticks"][0]) >= {"reported_mw", "deliverable_mw", "commanded_mw", "delivered_mw"}
    assert chart.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    import matplotlib.pyplot as plt
    image = plt.imread(chart)
    assert float(image.std()) > 0


def test_postgres_replay_skips_when_docker_is_unavailable(monkeypatch):
    monkeypatch.setattr(f"{__name__}.docker_ready", lambda: False)
    with pytest.raises(pytest.skip.Exception):
        require_docker()


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


# ECRS is called for the first five minutes and nothing else is.
_FORCED_ECRS = {
    "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
    "forced": [{"product": "ECRS", "start": "2026-03-08 00:00", "minutes": 5}],
}

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
