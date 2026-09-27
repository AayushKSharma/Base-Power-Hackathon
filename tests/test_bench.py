"""`harness bench`: throughput, recovery after a partial kill, and jobs re-run."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from psycopg import sql

from conftest import AFTER_SPRING_FORWARD, SPRING_FORWARD
from harness.cli import main
from harness.farm import DEFAULT_DSN
from test_farm import _write_scenario

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


@pytest.fixture(scope="module")
def postgres_server():
    """The compose Postgres, reused when another checkout already owns the port."""
    if not _docker_ready():
        pytest.skip("Docker is not available")
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
    return DEFAULT_DSN


@pytest.fixture
def farm_dsn(postgres_server):
    name = "bench_" + uuid.uuid4().hex
    with psycopg.connect(postgres_server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield postgres_server.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.mark.postgres
def test_bench_reports_throughput_recovery_and_reruns(farm_dsn, tmp_path, market_store, capsys):
    """A fixture sweep prints and saves scenario-days/min, recovery time, and jobs re-run."""
    scenario = _write_scenario(tmp_path, "narrow_cap", 100.0)
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text(
        "policies:\n"
        "  - builtin: constant_haircut\n"
        "    param: {fraction: '0.8'}\n"
        "scenarios:\n"
        f"  - {scenario.name}\n"
        f"start: {SPRING_FORWARD.isoformat()}\n"
        f"end: {AFTER_SPRING_FORWARD.isoformat()}\n"
        "seeds: [1]\n"
    )
    out = tmp_path / "bench"
    database = ["--database", farm_dsn]
    assert main(["submit", "--sweep", str(sweep), *database]) == 0
    capsys.readouterr()

    code = main([
        "bench",
        "--sweep", str(sweep),
        "--market-dir", str(market_store),
        "--database", farm_dsn,
        "--out", str(out),
        "--lease-seconds", "0.3",
    ])

    assert code == 0
    printed = capsys.readouterr().out
    saved = (out / "bench.md").read_text()
    assert saved in printed
    assert f"Wrote {out / 'bench.md'}" in printed

    for workers in (1, 8, 32):
        row = re.search(rf"(?m)^\| {workers} \| (\d+\.\d{{3}}) \|$", saved)
        assert row is not None, saved
        assert float(row.group(1)) > 0

    # 20% of 32 workers, nearest count, is 6. The kill happens while the sweep is running.
    recovery = re.search(r"(?m)^\| 32 \| 6 \| (\d+\.\d{3}) \| (\d+) \|$", saved)
    assert recovery is not None, saved
    assert float(recovery.group(1)) > 0
    assert int(recovery.group(2)) >= 1
    assert "Scenario-days/min" in saved
    assert "Time to finish (s)" in saved
    assert "Jobs re-run" in saved

    # Bench does not drain or replace a sweep already submitted on that database.
    assert main(["status", *database]) == 0
    status = capsys.readouterr().out
    assert "queued 2" in status
    assert "done 0" in status


@pytest.mark.postgres
def test_aggregated_sweep_matches_harness_compare(farm_dsn, tmp_path, market_store, capsys):
    """A finished sweep renders the same comparison table and charts as `harness compare`."""
    narrow = _write_scenario(tmp_path, "narrow_cap", 0.5)
    wide = _write_scenario(tmp_path, "wide_cap", 100.0)
    sweep = tmp_path / "sweep.yaml"
    start = SPRING_FORWARD.isoformat()
    end = AFTER_SPRING_FORWARD.isoformat()
    sweep.write_text(
        "policies:\n"
        "  - builtin: constant_haircut\n"
        "  - builtin: reliability_target\n"
        "scenarios:\n"
        f"  - {narrow.name}\n"
        f"  - {wide.name}\n"
        f"start: {start}\n"
        f"end: {end}\n"
        "seeds: [3]\n"
    )
    database = ["--database", farm_dsn]
    compare_dir = tmp_path / "compare"
    sweep_dir = tmp_path / "from-sweep"

    code = main([
        "compare",
        "--policy", "constant_haircut",
        "--policy", "reliability_target",
        "--scenario", str(narrow),
        "--scenario", str(wide),
        "--start", start,
        "--end", end,
        "--seed", "3",
        "--day", start,
        "--market-dir", str(market_store),
        "--out", str(compare_dir),
    ])
    assert code == 0
    table = capsys.readouterr().out.split("\nWrote ", 1)[0]

    assert main(["submit", "--sweep", str(sweep), *database]) == 0
    assert main(["work", "--market-dir", str(market_store), "--worker-id", "compare", *database]) == 0
    code = main(["aggregate", "--compare-out", str(sweep_dir), "--market-dir", str(market_store), *database])
    assert code == 0
    assert table in capsys.readouterr().out

    for name in ("comparison.md", "exceedance.md", "frontier.svg"):
        assert (sweep_dir / name).read_text() == (compare_dir / name).read_text()
    for name in ("frontier.png", "exceedance.png", "capability.png", "rankings.png"):
        assert (sweep_dir / "charts" / name).read_bytes() == (compare_dir / "charts" / name).read_bytes()
    got = pd.read_parquet(sweep_dir / "intervals.parquet")
    expected = pd.read_parquet(compare_dir / "intervals.parquet")
    pd.testing.assert_frame_equal(got, expected)
