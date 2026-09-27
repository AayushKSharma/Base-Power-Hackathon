"""Run farm: submit a sweep, run workers, aggregate scorecards, and read status."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from conftest import AFTER_SPRING_FORWARD, SPRING_FORWARD, recorded
from harness.cli import main
from harness.external import ExternalPolicy
from harness.farm import DEFAULT_DSN, ChaosReport, Clock, Farm, IncompleteSweep, PolicySpec, Sweep
from harness.policy import builtin_policy
from harness.runner import run
from harness.scenario import load_scenario

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


@pytest.fixture(scope="session")
def postgres_server():
    """The compose Postgres, reused when it is already accepting connections.

    A different checkout may already own the port. Connecting proves the harness
    database is there. Compose is started only when nothing is listening.
    """
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
def farm_dsn(postgres_server):
    name = "farm_" + uuid.uuid4().hex
    with psycopg.connect(postgres_server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield postgres_server.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

START = SPRING_FORWARD
END = AFTER_SPRING_FORWARD

_SCENARIO = """\
seed: 7
fleet:
  homes: 20
  regions: 4
  battery_kwh: 20
  inverter_kw: 10
  backup_floor: 0.2
  soc: {{fixed: 0.6}}
  telemetry_stale_s: 180
  state: quantile
  quantile_mock:
    shares: {{P10: 1.0, P25: 1.0, P50: 1.0, P75: 1.0, P90: 1.0}}
    policy_view: typical
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
  ECRS:
    duration_h: 1
    cap_mw: {cap_mw}
    cap_share: 0.9
  NONSPIN:
    duration_h: 4
    cap_mw: {cap_mw}
    cap_share: 0.9
"""


def _write_scenario(directory: Path, name: str, cap_mw: float) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(_SCENARIO.format(cap_mw=cap_mw))
    return path


@pytest.mark.postgres
def test_several_workers_match_sequential_harness_runs(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    scenarios = (
        _write_scenario(tmp_path, "narrow_cap", 0.5),
        _write_scenario(tmp_path, "wide_cap", 100.0),
    )
    policies = (
        PolicySpec(builtin="constant_haircut", params={"fraction": "0.8"}),
        PolicySpec(builtin="constant_haircut", params={"fraction": "0.5"}),
    )
    seeds = (1, 3)
    submitted = farm.submit(Sweep(policies, scenarios, START, END, seeds))

    assert submitted == len(policies) * len(scenarios) * 2 * len(seeds)

    errors: list[BaseException] = []

    def run_worker(worker_id: str) -> None:
        try:
            farm.work(worker_id)
        except BaseException as exc:  # report the worker's own failure, then fail the test
            errors.append(exc)

    threads = [threading.Thread(target=run_worker, args=(f"worker-{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    got = farm.aggregate()
    for spec, scenario_path, seed in (
        (spec, path, seed) for spec in policies for path in scenarios for seed in seeds
    ):
        scenario = load_scenario(scenario_path)
        assert spec.builtin is not None
        policy = builtin_policy(spec.builtin, spec.params, scenario)
        expected = run(policy, scenario, START, END, seed, market=recorded(market_store)).scorecards
        assert got[(policy.name, scenario.name, seed)] == expected

    report = farm.status()
    assert (report.queued, report.leased, report.done, report.failed) == (0, 0, submitted, 0)
    assert report.throughput_per_s > 0


class ManualClock(Clock):
    """A clock the lease test moves past an expiry without waiting."""

    def __init__(self) -> None:
        self.instant = dt.datetime(2026, 3, 8, tzinfo=dt.timezone.utc)

    def now(self) -> dt.datetime:
        return self.instant

    def advance(self, seconds: float) -> None:
        self.instant += dt.timedelta(seconds=seconds)


def _one_day(farm: Farm, directory: Path) -> tuple[PolicySpec, Path]:
    scenario = _write_scenario(directory, "narrow_cap", 100.0)
    spec = PolicySpec(builtin="constant_haircut", params={"fraction": "0.9"})
    assert farm.submit(Sweep((spec,), (scenario,), START, START, (1,))) == 1
    return spec, scenario


@pytest.mark.postgres
def test_an_expired_lease_cannot_be_completed_by_the_original_worker(farm_dsn, tmp_path, market_store):
    clock = ManualClock()
    farm = Farm(farm_dsn, market_dir=market_store, clock=clock, lease=dt.timedelta(seconds=30))
    spec, scenario_path = _one_day(farm, tmp_path)

    original = farm.claim("worker-a")
    assert original is not None
    clock.advance(31)
    reclaimed = farm.claim("worker-b")
    assert reclaimed is not None and reclaimed.attempt == original.attempt + 1

    assert farm.complete(original) is False
    assert farm.complete(reclaimed) is True

    scenario = load_scenario(scenario_path)
    assert spec.builtin is not None
    policy = builtin_policy(spec.builtin, spec.params, scenario)
    expected = run(policy, scenario, START, START, 1, market=recorded(market_store)).scorecards
    assert farm.aggregate()[(policy.name, scenario.name, 1)] == expected
    assert farm.status().done == 1


@pytest.mark.postgres
def test_completing_the_same_job_twice_leaves_one_result(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    spec, scenario_path = _one_day(farm, tmp_path)
    lease = farm.claim("worker-a")
    assert lease is not None

    assert farm.complete(lease) is True
    assert farm.complete(lease) is False

    scenario = load_scenario(scenario_path)
    assert spec.builtin is not None
    policy = builtin_policy(spec.builtin, spec.params, scenario)
    expected = run(policy, scenario, START, START, 1, market=recorded(market_store)).scorecards
    assert farm.aggregate()[(policy.name, scenario.name, 1)] == expected
    assert farm.status().done == 1


def test_postgres_tests_skip_when_docker_is_unavailable(monkeypatch):
    monkeypatch.setattr(f"{__name__}.docker_ready", lambda: False)
    with pytest.raises(pytest.skip.Exception):
        require_docker()


PID_POLICY = Path(__file__).resolve().parent / "policies" / "pid_policy.py"


def _external_sweep(directory: Path, *, crash_once: Path | None) -> tuple[Sweep, Path]:
    scenario = _write_scenario(directory, "narrow_cap", 100.0)
    log = directory / "pids.txt"
    command = [sys.executable, str(PID_POLICY), "--log", str(log), "--mw", "4"]
    if crash_once is not None:
        command += ["--crash-once", str(crash_once)]
    sweep = Sweep(
        (PolicySpec(command=tuple(command), version="1"),),
        (scenario,),
        START,
        END if crash_once is None else START,
        (1,),
    )
    return sweep, log


@pytest.mark.postgres
def test_a_worker_reuses_one_external_process_per_policy(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    sweep, log = _external_sweep(tmp_path, crash_once=None)
    assert farm.submit(sweep) == 2
    before = log.read_text().split()

    assert farm.work("worker-a") == 2

    worker_pids = log.read_text().split()[len(before):]
    assert len(worker_pids) == 1 and len(set(worker_pids)) == 1

    scenario = load_scenario(sweep.scenarios[0])
    command = sweep.policies[0].command
    assert command is not None
    with ExternalPolicy(list(command), scenario.products) as policy:
        expected = run(policy, scenario, START, END, 1, market=recorded(market_store)).scorecards
    assert farm.aggregate()[(policy.name, scenario.name, 1)] == expected


@pytest.mark.postgres
def test_a_worker_restarts_an_external_policy_after_it_crashes(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    sweep, log = _external_sweep(tmp_path, crash_once=tmp_path / "crashed")
    assert farm.submit(sweep) == 1
    before = log.read_text().split()

    assert farm.work("worker-a") == 1

    assert len(log.read_text().split()) - len(before) == 2
    assert farm.status().done == 1


@pytest.mark.postgres
def test_cli_submit_work_aggregate_and_status(farm_dsn, tmp_path, market_store, capsys):
    scenario = _write_scenario(tmp_path, "narrow_cap", 100.0)
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text(
        "policies:\n"
        "  - builtin: constant_haircut\n"
        "    param: {fraction: '0.8'}\n"
        "scenarios:\n"
        f"  - {scenario.name}\n"
        "start: 2026-03-08\n"
        "end: 2026-03-08\n"
        "seeds: [1]\n"
    )
    database = ["--database", farm_dsn]

    assert main(["submit", "--sweep", str(sweep), *database]) == 0
    assert main(["work", "--market-dir", str(market_store), "--worker-id", "cli", *database]) == 0
    out = tmp_path / "cards"
    assert main(["aggregate", "--out", str(out), *database]) == 0
    assert main(["status", *database]) == 0

    printed = capsys.readouterr().out
    assert "submitted 1 jobs" in printed
    assert "cli completed 1 jobs" in printed
    assert "aggregated 1 scorecards" in printed
    assert "done 1" in printed and "queued 0" in printed
    written = list(out.glob("*.json"))
    assert len(written) == 1
    assert "constant_haircut(fraction=0.8)" in written[0].read_text()


@pytest.mark.postgres
def test_chaos_command_kills_workers_and_keeps_the_scorecard(farm_dsn, tmp_path, market_store, capsys):
    scenario = _write_scenario(tmp_path, "narrow_cap", 100.0)
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text(
        "policies:\n"
        "  - builtin: constant_haircut\n"
        "    param: {fraction: '0.8'}\n"
        "scenarios:\n"
        f"  - {scenario.name}\n"
        "start: 2026-03-08\n"
        "end: 2026-03-08\n"
        "seeds: [1]\n"
    )
    assert main([
        "chaos", "--sweep", str(sweep), "--seed", "11", "--workers", "2",
        "--lease-seconds", "0.4", "--market-dir", str(market_store),
        "--database", farm_dsn,
    ]) == 0

    printed = capsys.readouterr().out
    assert "mid-job" in printed
    assert "before commit" in printed

    farm = Farm(farm_dsn, market_dir=market_store)
    loaded = load_scenario(scenario)
    policy = builtin_policy("constant_haircut", {"fraction": "0.8"}, loaded)
    expected = run(policy, loaded, START, START, 1, market=recorded(market_store)).scorecards
    assert farm.aggregate()[(policy.name, loaded.name, 1)] == expected


@pytest.mark.postgres
def test_a_raising_job_fails_after_max_attempts_and_the_rest_completes(
    farm_dsn, tmp_path, market_store,
):
    clock = ManualClock()
    farm = Farm(
        farm_dsn, market_dir=market_store, clock=clock,
        max_attempts=3, backoff=dt.timedelta(seconds=10),
    )
    good = _write_scenario(tmp_path, "wide_cap", 100.0)
    poison = _write_scenario(tmp_path, "narrow_cap", 0.5)
    spec = PolicySpec(builtin="constant_haircut", params={"fraction": "0.9"})
    assert farm.submit(Sweep((spec,), (good, poison), START, START, (1,))) == 2
    poison.write_text("this: is: not: a scenario:\n  - [\n")

    assert farm.work("worker-a") == 1
    waiting = farm.status()
    assert (waiting.done, waiting.failed, waiting.queued, waiting.leased) == (1, 0, 1, 0)

    clock.advance(9)
    assert farm.work("worker-a") == 0
    assert farm.status().failed == 0

    clock.advance(1)
    assert farm.work("worker-a") == 0
    assert farm.status().queued == 1

    clock.advance(19)
    assert farm.work("worker-a") == 0
    assert farm.status().failed == 0

    clock.advance(1)
    assert farm.work("worker-a") == 0
    report = farm.status()
    assert (report.done, report.failed, report.queued, report.leased) == (1, 1, 0, 0)
    assert len(report.errors) == 1
    assert "not valid YAML" in report.errors[0]


@pytest.mark.postgres
def test_resubmitting_a_sweep_adds_only_changed_scenarios(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    scenario = _write_scenario(tmp_path, "narrow_cap", 0.5)
    spec = PolicySpec(builtin="constant_haircut", params={"fraction": "0.8"})
    sweep = Sweep((spec,), (scenario,), START, END, (1, 3))
    assert farm.submit(sweep) == 4

    assert farm.submit(sweep) == 0
    assert farm.status().queued == 4

    _write_scenario(tmp_path, "narrow_cap", 100.0)
    assert farm.submit(sweep) == 4
    assert farm.status().queued == 8


def _assert_matches_sequential(farm: Farm, spec: PolicySpec, scenario_path: Path, market_store: Path) -> None:
    got = farm.aggregate()
    scenario = load_scenario(scenario_path)
    assert spec.builtin is not None
    policy = builtin_policy(spec.builtin, spec.params, scenario)
    for seed in (1, 3):
        expected = run(policy, scenario, START, END, seed, market=recorded(market_store)).scorecards
        assert got[(policy.name, scenario.name, seed)] == expected


@pytest.mark.postgres
def test_workers_killed_mid_job_match_sequential_runs(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store, lease=dt.timedelta(milliseconds=400))
    scenario = _write_scenario(tmp_path, "narrow_cap", 100.0)
    spec = PolicySpec(builtin="constant_haircut", params={"fraction": "0.8"})
    assert farm.submit(Sweep((spec,), (scenario,), START, END, (1, 3))) == 4

    report = farm.chaos(workers=3, seed=11, kill="mid")

    assert isinstance(report, ChaosReport)
    assert report.killed_mid_job >= 1
    assert report.killed_before_commit == 0
    assert report.committed == 4
    _assert_matches_sequential(farm, spec, scenario, market_store)
    assert farm.status().failed == 0


@pytest.mark.postgres
def test_workers_killed_before_commit_match_sequential_runs(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store, lease=dt.timedelta(milliseconds=400))
    scenario = _write_scenario(tmp_path, "narrow_cap", 100.0)
    spec = PolicySpec(builtin="constant_haircut", params={"fraction": "0.8"})
    assert farm.submit(Sweep((spec,), (scenario,), START, START, (1,))) == 1

    report = farm.chaos(workers=2, seed=11, kill="both")

    assert report.killed_mid_job >= 1
    assert report.killed_before_commit >= 1
    assert report.committed == 1
    got = farm.aggregate()
    loaded = load_scenario(scenario)
    assert spec.builtin is not None
    policy = builtin_policy(spec.builtin, spec.params, loaded)
    expected = run(policy, loaded, START, START, 1, market=recorded(market_store)).scorecards
    assert got[(policy.name, loaded.name, 1)] == expected
    assert farm.status().failed == 0


@pytest.mark.postgres
def test_aggregate_refuses_an_incomplete_sweep(farm_dsn, tmp_path, market_store):
    farm = Farm(farm_dsn, market_dir=market_store)
    _one_day(farm, tmp_path)

    with pytest.raises(IncompleteSweep, match="incomplete"):
        farm.aggregate()
