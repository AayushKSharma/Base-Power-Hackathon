"""Measure the run farm: throughput, recovery after a partial kill, wasted work.

A scenario-day is one completed job: a policy, a scenario, an operating day,
and a seed. Throughput is scenario-days per minute at each worker count.
Recovery kills 20% of the workers (nearest count) once the sweep is underway,
then times how long the survivors take to finish. Jobs re-run are the extra
attempts those kills caused.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

from harness.farm import Farm, Lease, Sweep, WorkerKilled

# Default worker counts the benchmark always reports.
WORKER_COUNTS = (1, 8, 32)
KILL_FRACTION = 0.2


def render_bench(
    rates: list[tuple[int, float]],
    *,
    workers: int,
    killed: int,
    finish_s: float,
    jobs_rerun: int,
) -> str:
    """The benchmark table, stable enough to drop into the README."""
    lines = [
        "# Farm benchmark",
        "",
        "One scenario-day is one completed job: a policy, a scenario, an operating day, and a seed.",
        "",
        "## Throughput",
        "",
        "| Workers | Scenario-days/min |",
        "| ---: | ---: |",
    ]
    lines += [f"| {count} | {rate:.3f} |" for count, rate in rates]
    lines += [
        "",
        "## Recovery",
        "",
        "20% of the workers, rounded to the nearest count, are killed partway through the sweep. "
        "Time to finish is wall time from that kill until every job is done. "
        "Jobs re-run counts extra attempts.",
        "",
        "| Workers | Killed | Time to finish (s) | Jobs re-run |",
        "| ---: | ---: | ---: | ---: |",
        f"| {workers} | {killed} | {finish_s:.3f} | {jobs_rerun} |",
        "",
    ]
    return "\n".join(lines)


def run_bench(farm: Farm, sweep: Sweep, worker_counts: tuple[int, ...] = WORKER_COUNTS) -> str:
    """Run every measurement on a fresh queue and return the saved table text."""
    rates = [(count, _throughput(farm, sweep, count)) for count in worker_counts]
    workers = max(worker_counts)
    killed, finish_s, rerun = _recover(farm, sweep, workers, KILL_FRACTION)
    return render_bench(rates, workers=workers, killed=killed, finish_s=finish_s, jobs_rerun=rerun)


def _throughput(farm: Farm, sweep: Sweep, workers: int) -> float:
    farm.reset()
    jobs = farm.submit(sweep)
    if jobs < 1:
        raise ValueError("sweep submitted no jobs")
    started = time.perf_counter()
    _run_workers(farm, workers, prefix=f"bench-{workers}")
    _require_done(farm)
    elapsed = time.perf_counter() - started
    return jobs / (elapsed / 60.0)


def _recover(farm: Farm, sweep: Sweep, workers: int, fraction: float) -> tuple[int, float, int]:
    """Kill `fraction` of the workers after the sweep has started. Return killed, seconds, re-runs."""
    farm.reset()
    jobs = farm.submit(sweep)
    if jobs < 1:
        raise ValueError("sweep submitted no jobs")
    kill_n = int(workers * fraction + 0.5)
    plan = _PartialKill(farm, workers, kill_n)
    farm.watch_jobs(plan.on_job)
    try:
        _finish_after_kills(farm, plan)
    finally:
        farm.watch_jobs(None)
    if plan.t_kill is None:
        raise RuntimeError("workers finished before any of them could be killed")
    _require_done(farm)
    return kill_n, time.perf_counter() - plan.t_kill, farm.jobs_rerun()


class _PartialKill:
    """The second job to start is partway through the sweep: kill 20% there."""

    def __init__(self, farm: Farm, workers: int, kill_n: int):
        self._farm = farm
        self._ids = tuple(f"recover-{index}" for index in range(workers))
        self._kill_n = kill_n
        self._started = 0
        self._lock = threading.Lock()
        self.killed: frozenset[str] = frozenset()
        self.t_kill: float | None = None

    def on_job(self, lease: Lease) -> None:
        die = False
        with self._lock:
            if self.killed:
                die = lease.owner in self.killed
            else:
                self._started += 1
                if self._started >= 2 and self._kill_n > 0:
                    others = [worker_id for worker_id in self._ids if worker_id != lease.owner]
                    chosen = [lease.owner, *others[: self._kill_n - 1]]
                    self.killed = frozenset(chosen)
                    self._farm.doom(set(chosen))
                    self.t_kill = time.perf_counter()
                    die = True
        if die:
            raise WorkerKilled(f"worker {lease.owner} killed partway through the sweep")


def _finish_after_kills(farm: Farm, plan: _PartialKill) -> None:
    for _ in range(64):
        state = farm.status()
        if state.queued == 0 and state.leased == 0 and state.done > 0:
            return
        alive = [worker_id for worker_id in plan._ids if worker_id not in plan.killed]
        if not alive:
            raise RuntimeError("every worker was killed")
        _run_workers(farm, alive)
        state = farm.status()
        if state.queued == 0 and state.leased == 0:
            return
        time.sleep(farm.lease.total_seconds() + 0.05)
    raise RuntimeError("recovery made no progress")


def _run_workers(farm: Farm, workers: int | list[str], *, prefix: str = "bench") -> None:
    if isinstance(workers, int):
        ids = [f"{prefix}-{index}" for index in range(workers)]
    else:
        ids = workers
    errors: list[BaseException] = []

    def run_worker(worker_id: str) -> None:
        try:
            farm.work(worker_id)
        except WorkerKilled:
            return
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_worker, args=(worker_id,)) for worker_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def _require_done(farm: Farm) -> None:
    state = farm.status()
    if state.failed:
        raise RuntimeError(f"sweep failed {state.failed} jobs: {state.errors[0] if state.errors else ''}")
    if state.queued or state.leased:
        raise RuntimeError(f"sweep still running: queued {state.queued}, leased {state.leased}")


@contextmanager
def temporary_database(dsn: str) -> Iterator[str]:
    """A fresh database on the same server. The caller's database is not touched."""
    name = "bench_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = dsn.rsplit("/", 1)[0] + "/" + name
    try:
        yield url
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def write_bench(text: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "bench.md"
    path.write_text(text)
    return path
