"""Run farm: expand a sweep into day-jobs, lease them out, and score the results.

A sweep is policies × scenarios × operating days × seeds. Each combination is
one job. Workers claim jobs with ``SELECT … FOR UPDATE SKIP LOCKED`` under a
time-limited lease, run the harness for that one day, and commit the result in
the same transaction that marks the job done. The result's unique key is the
job identity: policy name, policy version, scenario content hash, day, and seed.
A raised job is retried after ``not_before``, then failed. A killed worker
leaves the lease to expire, so the day is run again and written once.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import random
import shlex
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import psycopg
import yaml
from psycopg.rows import dict_row

from harness.external import ExternalPolicy
from harness.market import load_intervals
from harness.observation import Observation
from harness.policy import Capability, PolicyError, builtin_policy
from harness.runner import RunResult, run
from harness.scenario import Scenario, load_scenario
from harness.scorecard import DayResult, FaultCounts, ProductTotals, Scorecard

DEFAULT_DSN = "postgresql://harness:harness@127.0.0.1:54329/harness"
_LEASE = dt.timedelta(seconds=30)
_MAX_ATTEMPTS = 3
_BACKOFF = dt.timedelta(seconds=1)

_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_name text NOT NULL,
    policy_version text NOT NULL,
    scenario_hash text NOT NULL,
    scenario_name text NOT NULL,
    scenario_path text NOT NULL,
    day date NOT NULL,
    seed integer NOT NULL,
    builtin text,
    params text NOT NULL,
    command text,
    timeout_s double precision NOT NULL,
    fallback text NOT NULL,
    status text NOT NULL,
    lease_owner text,
    lease_expires timestamptz,
    attempt integer NOT NULL DEFAULT 0,
    not_before timestamptz,
    error text,
    created_at timestamptz NOT NULL,
    finished_at timestamptz,
    UNIQUE (policy_name, policy_version, scenario_hash, day, seed)
)
"""

_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    policy_name text NOT NULL,
    policy_version text NOT NULL,
    scenario_hash text NOT NULL,
    day date NOT NULL,
    seed integer NOT NULL,
    scorecards text NOT NULL,
    intervals bytea,
    PRIMARY KEY (policy_name, policy_version, scenario_hash, day, seed)
)
"""


@dataclass(frozen=True)
class PolicySpec:
    """One policy in a sweep: a built-in, or an external command.

    ``version`` is part of the job identity. Built-ins default to ``"1"``.
    """

    builtin: str | None = None
    params: Mapping[str, str] = field(default_factory=dict)
    command: tuple[str, ...] | None = None
    version: str = "1"
    timeout_s: float = 1.0
    fallback: str = "last_good"

    def __post_init__(self) -> None:
        has_builtin = self.builtin is not None
        has_command = bool(self.command)
        if has_builtin == has_command:
            raise ValueError("a policy is either a built-in name or an external command")


@dataclass(frozen=True)
class Sweep:
    """Policies × scenarios × an inclusive date range × seeds."""

    policies: tuple[PolicySpec, ...]
    scenarios: tuple[Path, ...]
    start: dt.date
    end: dt.date
    seeds: tuple[int, ...]


def load_sweep(path: Path | str) -> Sweep:
    """Read a sweep YAML file. Scenario paths are resolved next to the file, or as presets."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping")
    unknown = sorted(set(raw) - {"policies", "scenarios", "start", "end", "seeds"})
    if unknown:
        raise ValueError(f"{path}: unknown field {unknown[0]!r}")
    policies_raw = raw.get("policies")
    scenarios_raw = raw.get("scenarios")
    seeds_raw = raw.get("seeds")
    if not isinstance(policies_raw, list) or not policies_raw:
        raise ValueError(f"{path}: policies must be a non-empty list")
    if not isinstance(scenarios_raw, list) or not scenarios_raw:
        raise ValueError(f"{path}: scenarios must be a non-empty list")
    if not isinstance(seeds_raw, list) or not seeds_raw:
        raise ValueError(f"{path}: seeds must be a non-empty list")
    return Sweep(
        tuple(_policy_spec(item) for item in policies_raw),
        tuple(_scenario_path(path, item) for item in scenarios_raw),
        _as_date(raw.get("start"), "start"),
        _as_date(raw.get("end"), "end"),
        tuple(_as_seed(item) for item in seeds_raw),
    )


def _scenario_path(sweep_file: Path, spec: Any) -> Path:
    from harness.scenario import resolve_scenario

    if not isinstance(spec, str) or not spec:
        raise ValueError(f"scenario must be a path or preset name, got {spec!r}")
    beside = sweep_file.parent / spec
    if beside.is_file():
        return beside.resolve()
    return resolve_scenario(spec)


def _policy_spec(item: Any) -> PolicySpec:
    if not isinstance(item, dict):
        raise ValueError(f"a policy must be a mapping, got {item!r}")
    unknown = sorted(set(item) - {"builtin", "param", "command", "version", "timeout_s", "fallback"})
    if unknown:
        raise ValueError(f"policy: unknown field {unknown[0]!r}")
    version = str(item.get("version", "1"))
    timeout = float(item.get("timeout_s", 1.0))
    fallback = str(item.get("fallback", "last_good"))
    if "command" in item and "builtin" in item:
        raise ValueError("a policy is either a built-in or a command")
    if "builtin" in item:
        params = item.get("param") or {}
        if not isinstance(params, dict):
            raise ValueError("param must be a mapping of strings")
        return PolicySpec(
            builtin=str(item["builtin"]),
            params={str(key): str(value) for key, value in params.items()},
            version=version, timeout_s=timeout, fallback=fallback,
        )
    command = item.get("command")
    if isinstance(command, str):
        argv = tuple(shlex.split(command))
    elif isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        argv = tuple(command)
    else:
        raise ValueError("command must be a string or a list of strings")
    return PolicySpec(command=argv, version=version, timeout_s=timeout, fallback=fallback)


def _as_date(value: Any, name: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")


def _as_seed(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"seed must be a non-negative integer, got {value!r}")
    return value


@dataclass(frozen=True)
class Lease:
    """A worker's claim on one job, including the attempt the fence checks."""

    job_id: int
    owner: str
    attempt: int


@dataclass(frozen=True)
class FarmStatus:
    """How many jobs sit in each state, and completed jobs per second."""

    queued: int
    leased: int
    done: int
    failed: int
    throughput_per_s: float
    errors: tuple[str, ...] = ()


class IncompleteSweep(ValueError):
    """Aggregate was asked for a sweep that is not entirely done."""


class WorkerKilled(Exception):
    """Chaos stopped this worker after it claimed a job and before it committed."""


@dataclass(frozen=True)
class ChaosReport:
    """How many jobs were committed, and how many workers chaos killed."""

    committed: int
    killed_mid_job: int
    killed_before_commit: int


class _ChaosPlan:
    """Seeded choices of which claims die, and in which phase.

    The first claims take each kill window this run asked for. One later
    attempt may take a window the first pass never hit, so a single job still
    dies after the scorecard is computed. The attempt after that finishes.
    """

    def __init__(self, seed: int, kill: str):
        if kill not in {"mid", "before_commit", "both"}:
            raise ValueError(f"kill must be mid, before_commit, or both, got {kill!r}")
        self._rng = random.Random(seed)
        self.kill = kill
        self.killed_mid_job = 0
        self.killed_before_commit = 0
        self._lock = threading.Lock()
        self._saw_mid = False
        self._saw_commit = False
        self._retry_spent = False

    def decision(self, attempt: int) -> str | None:
        with self._lock:
            if attempt != 1:
                # One later attempt may take a window the first pass never hit,
                # so a single job still dies after computing and before committing.
                # The attempt after that one finishes.
                if self._retry_spent or not self._missing():
                    return None
                self._retry_spent = True
                return self._choose(self._missing()[0])
            missing = self._missing()
            if missing:
                return self._choose(missing[0])
            if self._rng.randrange(2) == 0:
                return None
            return self._choose(self._allowed()[self._rng.randrange(len(self._allowed()))])

    def _allowed(self) -> list[str]:
        windows = []
        if self.kill in {"mid", "both"}:
            windows.append("mid")
        if self.kill in {"before_commit", "both"}:
            windows.append("before_commit")
        return windows

    def _missing(self) -> list[str]:
        missing = []
        if "mid" in self._allowed() and not self._saw_mid:
            missing.append("mid")
        if "before_commit" in self._allowed() and not self._saw_commit:
            missing.append("before_commit")
        return missing

    def _choose(self, choice: str) -> str:
        if choice == "mid":
            self._saw_mid = True
            self.killed_mid_job += 1
        else:
            self._saw_commit = True
            self.killed_before_commit += 1
        return choice


class Clock:
    """Time the lease uses. Tests pass a clock they can move."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


class Farm:
    """One Postgres queue. Each call opens its own connection, so workers can share a Farm."""

    def __init__(self, dsn: str, *, market_dir: Path | None,
                 lease: dt.timedelta = _LEASE, clock: Clock | None = None,
                 max_attempts: int = _MAX_ATTEMPTS, backoff: dt.timedelta = _BACKOFF):
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        if backoff < dt.timedelta(0):
            raise ValueError(f"backoff must not be negative, got {backoff}")
        self.dsn = dsn
        self.market_dir = None if market_dir is None else Path(market_dir)
        self.lease = lease
        self.clock = clock or Clock()
        self.max_attempts = max_attempts
        self.backoff = backoff
        self._chaos: _ChaosPlan | None = None
        self._on_job_start: Callable[[Lease], None] | None = None
        self._doomed: frozenset[str] = frozenset()

    def submit(self, sweep: Sweep) -> int:
        """Insert one queued job per (policy, scenario, day, seed). Return how many were new."""
        if sweep.start > sweep.end:
            raise ValueError(f"start {sweep.start} is after end {sweep.end}")
        inserted = 0
        with self._connect() as conn:
            with conn.transaction():
                self._ensure(conn)
                for spec in sweep.policies:
                    for scenario_path in sweep.scenarios:
                        scenario = load_scenario(scenario_path)
                        identity = _identity(spec, scenario)
                        digest = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
                        day = sweep.start
                        while day <= sweep.end:
                            for seed in sweep.seeds:
                                row = conn.execute(
                                    """
                                    INSERT INTO jobs (
                                        policy_name, policy_version, scenario_hash, scenario_name,
                                        scenario_path, day, seed, builtin, params, command,
                                        timeout_s, fallback, status, created_at
                                    ) VALUES (
                                        %(policy_name)s, %(policy_version)s, %(scenario_hash)s, %(scenario_name)s,
                                        %(scenario_path)s, %(day)s, %(seed)s, %(builtin)s, %(params)s, %(command)s,
                                        %(timeout_s)s, %(fallback)s, 'queued', %(created_at)s
                                    )
                                    ON CONFLICT (policy_name, policy_version, scenario_hash, day, seed)
                                    DO NOTHING
                                    RETURNING id
                                    """,
                                    {
                                        **identity,
                                        "scenario_hash": digest,
                                        "scenario_name": scenario.name,
                                        "scenario_path": str(scenario_path.resolve()),
                                        "day": day,
                                        "seed": seed,
                                        "created_at": self.clock.now(),
                                    },
                                ).fetchone()
                                if row is not None:
                                    inserted += 1
                            day += dt.timedelta(days=1)
        return inserted

    def claim(self, worker_id: str) -> Lease | None:
        """Claim the oldest due job, or one whose lease has expired.

        A queued job whose ``not_before`` is still in the future is left for later.
        """
        now = self.clock.now()
        with self._connect() as conn:
            with conn.transaction():
                self._ensure(conn)
                row = conn.execute(
                    """
                    WITH candidate AS (
                        SELECT id FROM jobs
                        WHERE (status = 'queued' AND (not_before IS NULL OR not_before <= %s))
                           OR (status = 'leased' AND lease_expires <= %s)
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE jobs AS job
                    SET status = 'leased',
                        lease_owner = %s,
                        lease_expires = %s,
                        attempt = job.attempt + 1
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.id, job.attempt
                    """,
                    (now, now, worker_id, now + self.lease),
                ).fetchone()
        if row is None:
            return None
        return Lease(job_id=int(row["id"]), owner=worker_id, attempt=int(row["attempt"]))

    def complete(self, lease: Lease, *, policies: _PolicyPool | None = None) -> bool:
        """Run this job's day and commit its scorecards if the lease still belongs to the caller.

        The harness run happens outside the commit transaction. The result insert
        and the status update are one transaction, and they are written only when
        the lease owner and attempt number still match. `policies`, when given,
        supplies the worker's reused external processes.
        """
        job = self._job(lease.job_id)
        hook = self._on_job_start
        if hook is not None:
            hook(lease)
        decision = None if self._chaos is None else self._chaos.decision(lease.attempt)
        if decision == "mid":
            raise WorkerKilled(f"worker {lease.owner} killed mid-job {lease.job_id}")
        scenario = load_scenario(job["scenario_path"])
        policy = _policy(job, scenario) if policies is None else policies.policy(job, scenario)
        with self._renewing(lease):
            result = run(
                policy, scenario, job["day"], job["day"], int(job["seed"]),
                market=lambda start, end: load_intervals(start, end, store_dir=self.market_dir),
            )
        payload = json.dumps([card.to_dict() for card in result.scorecards.values()])
        if decision == "before_commit":
            raise WorkerKilled(f"worker {lease.owner} killed before committing job {lease.job_id}")
        dump = io.BytesIO()
        result.intervals.to_parquet(dump, index=False)
        return self._commit(job, lease, payload, dump.getvalue())

    def chaos(self, *, workers: int, seed: int, kill: str = "both") -> ChaosReport:
        """Run workers, killing them at seeded points, until every job is terminal.

        ``kill`` is ``mid`` (while the job is held and before a result exists),
        ``before_commit`` (after the scorecard is computed, before the result
        row is written), or ``both``. A killed worker leaves its lease to
        expire; a later attempt commits the only result.
        """
        if workers < 1:
            raise ValueError(f"workers must be at least 1, got {workers}")
        plan = _ChaosPlan(seed, kill)
        self._chaos = plan
        try:
            for generation in range(64):
                state = self.status()
                if state.queued == 0 and state.leased == 0:
                    return ChaosReport(state.done, plan.killed_mid_job, plan.killed_before_commit)
                errors: list[BaseException] = []

                def run_worker(worker_id: str) -> None:
                    try:
                        self.work(worker_id)
                    except WorkerKilled:
                        return
                    except Exception as exc:
                        errors.append(exc)

                threads = [
                    threading.Thread(target=run_worker, args=(f"chaos-{seed}-{generation}-{index}",))
                    for index in range(workers)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                if errors:
                    raise errors[0]
                state = self.status()
                if state.queued == 0 and state.leased == 0:
                    return ChaosReport(state.done, plan.killed_mid_job, plan.killed_before_commit)
                # Dead workers still hold leases, or a retry is waiting on not_before.
                time.sleep(self.lease.total_seconds() + 0.1)
            raise RuntimeError("chaos made no progress")
        finally:
            self._chaos = None

    def work(self, worker_id: str) -> int:
        """Claim and complete jobs until none are waiting. Return how many this worker committed.

        One external policy process is kept per command and reused across that
        worker's jobs. A crash still starts a new process, as the harness does
        for a single run. A job whose harness run raises is queued again after
        an exponential backoff, up to `max_attempts`, and then marked failed with
        the last error. The worker continues either way. Processes are closed
        when it stops.
        """
        committed = 0
        policies = _PolicyPool()
        try:
            while True:
                if worker_id in self._doomed:
                    return committed
                lease = self.claim(worker_id)
                if lease is None:
                    return committed
                try:
                    if self.complete(lease, policies=policies):
                        committed += 1
                except WorkerKilled:
                    raise
                except Exception as exc:
                    # The error stays on the job. The worker keeps claiming so one
                    # failure does not abandon the rest of the queue.
                    self._fail(lease, exc)
        finally:
            policies.close()

    def aggregate(self) -> dict[tuple[str, str, int], dict[str, Scorecard]]:
        """Scorecards per (policy, scenario, seed), combined from per-day sums and counts.

        Days of two policy versions are not combined: the version is part of job identity.
        A sweep with queued, leased, or failed jobs is incomplete, and aggregate refuses it.
        """
        with self._connect() as conn:
            with conn.transaction():
                self._ensure(conn)
                report = self._counts(conn)
                if report["queued"] or report["leased"] or report["failed"]:
                    raise IncompleteSweep(
                        f"sweep is incomplete: queued {report['queued']}, "
                        f"leased {report['leased']}, failed {report['failed']}"
                    )
                rows = conn.execute("SELECT policy_version, scorecards FROM results").fetchall()
        grouped: dict[tuple[str, str, str, int], dict[str, list[Scorecard]]] = {}
        for row in rows:
            version = str(row["policy_version"])
            for raw in json.loads(row["scorecards"]):
                card = _scorecard(raw)
                cases = grouped.setdefault((card.policy, version, card.scenario, card.seed), {})
                cases.setdefault(card.fleet_case, []).append(card)
        out: dict[tuple[str, str, int], dict[str, Scorecard]] = {}
        for (policy, _version, scenario, seed), cases in grouped.items():
            key = (policy, scenario, seed)
            if key in out:
                raise ValueError(f"{policy} on {scenario} seed {seed} has more than one policy version")
            out[key] = {case: Scorecard.combine(cards) for case, cards in cases.items()}
        return out

    def runs(self) -> tuple[dt.date, dt.date, int, list[tuple[Scenario, list[RunResult]]]]:
        """One `RunResult` per policy and scenario, in submit order, over this sweep's days.

        Scorecards are the combined per-day results. The interval dump is the per-day
        dumps concatenated in day order, which is what one `harness compare` run stores.
        The sweep must be finished, and it must use a single seed: `harness compare`
        scores one seed.
        """
        cards = self.aggregate()
        with self._connect() as conn:
            self._ensure(conn)
            rows = conn.execute(
                """
                SELECT j.id, j.policy_name, j.scenario_name, j.scenario_path, j.day, j.seed,
                       r.intervals
                FROM jobs AS j
                JOIN results AS r
                  ON r.policy_name = j.policy_name
                 AND r.policy_version = j.policy_version
                 AND r.scenario_hash = j.scenario_hash
                 AND r.day = j.day
                 AND r.seed = j.seed
                ORDER BY j.id
                """
            ).fetchall()
        if not rows:
            raise IncompleteSweep("sweep has no results")
        blobs: dict[tuple[str, str, int], list[tuple[dt.date, memoryview | bytes]]] = {}
        paths: dict[str, str] = {}
        order: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        seeds: set[int] = set()
        days: list[dt.date] = []
        for row in rows:
            seed = int(row["seed"])
            seeds.add(seed)
            day = row["day"]
            if isinstance(day, dt.datetime):
                day = day.date()
            days.append(day)
            if row["intervals"] is None:
                raise ValueError(
                    f"{row['policy_name']} on {row['scenario_name']} {day} has no interval dump"
                )
            key = (str(row["policy_name"]), str(row["scenario_name"]), seed)
            blobs.setdefault(key, []).append((day, row["intervals"]))
            paths[str(row["scenario_name"])] = str(row["scenario_path"])
            pair = (str(row["scenario_name"]), str(row["policy_name"]))
            if pair not in seen:
                seen.add(pair)
                order.append(pair)
        if len(seeds) != 1:
            raise ValueError(f"compare renders one seed; this sweep has {sorted(seeds)}")
        seed = seeds.pop()
        policies_for: dict[str, list[str]] = {}
        for scenario_name, policy_name in order:
            policies_for.setdefault(scenario_name, []).append(policy_name)
        groups: list[tuple[Scenario, list[RunResult]]] = []
        for scenario_name in dict.fromkeys(scenario for scenario, _ in order):
            scenario = load_scenario(paths[scenario_name])
            results = []
            for policy_name in policies_for[scenario_name]:
                parts = sorted(blobs[(policy_name, scenario_name, seed)], key=lambda item: item[0])
                frames = [pd.read_parquet(io.BytesIO(bytes(blob))) for _, blob in parts]
                intervals = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
                results.append(RunResult(cards[(policy_name, scenario_name, seed)], intervals))
            groups.append((scenario, results))
        return min(days), max(days), seed, groups

    def reset(self) -> None:
        """Delete every job and result so the next measurement starts empty."""
        with self._connect() as conn:
            with conn.transaction():
                self._ensure(conn)
                conn.execute("DELETE FROM results")
                conn.execute("DELETE FROM jobs")
        self._doomed = frozenset()
        self._on_job_start = None

    def watch_jobs(self, hook: Callable[[Lease], None] | None) -> None:
        """Called at the start of each claimed job. A hook may raise `WorkerKilled`."""
        self._on_job_start = hook

    def doom(self, owners: set[str]) -> None:
        """These workers stop claiming. A job they already hold waits out its lease."""
        self._doomed = frozenset(owners)

    def jobs_rerun(self) -> int:
        """Extra attempts: each claim after the first is work that was thrown away."""
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                "SELECT COALESCE(SUM(attempt - 1), 0) AS n FROM jobs"
            ).fetchone()
        return 0 if row is None else int(row["n"])

    def status(self) -> FarmStatus:
        """Queued, leased, done and failed counts, plus completed jobs per second since submit."""
        with self._connect() as conn:
            with conn.transaction():
                self._ensure(conn)
                counts = self._counts(conn)
                errors = tuple(
                    str(row["error"])
                    for row in conn.execute(
                        "SELECT error FROM jobs WHERE status = 'failed' AND error IS NOT NULL ORDER BY id"
                    ).fetchall()
                )
                span = conn.execute("SELECT min(created_at) AS started FROM jobs").fetchone()
        started = None if span is None else span["started"]
        done = counts["done"]
        if started is None or done == 0:
            rate = 0.0
        else:
            elapsed = (self.clock.now() - started).total_seconds()
            rate = done / elapsed if elapsed > 0 else float(done)
        return FarmStatus(counts["queued"], counts["leased"], done, counts["failed"], rate, errors)

    @staticmethod
    def _counts(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, int]:
        counts = {"queued": 0, "leased": 0, "done": 0, "failed": 0}
        counted = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
        for row in counted:
            counts[str(row["status"])] = int(row["n"])
        return counts

    def _commit(self, job: Mapping[str, Any], lease: Lease, payload: str, intervals: bytes) -> bool:
        with self._connect() as conn:
            with conn.transaction():
                locked = conn.execute(
                    "SELECT lease_owner, attempt, status FROM jobs WHERE id = %s FOR UPDATE",
                    (lease.job_id,),
                ).fetchone()
                if (locked is None or locked["lease_owner"] != lease.owner
                        or int(locked["attempt"]) != lease.attempt or locked["status"] != "leased"):
                    return False
                conn.execute(
                    """
                    INSERT INTO results (
                        policy_name, policy_version, scenario_hash, day, seed, scorecards, intervals
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (policy_name, policy_version, scenario_hash, day, seed) DO NOTHING
                    """,
                    (job["policy_name"], job["policy_version"], job["scenario_hash"],
                     job["day"], job["seed"], payload, intervals),
                )
                updated = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'done', lease_owner = NULL, lease_expires = NULL, finished_at = %s
                    WHERE id = %s AND lease_owner = %s AND attempt = %s AND status = 'leased'
                    """,
                    (self.clock.now(), lease.job_id, lease.owner, lease.attempt),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(f"job {lease.job_id} lost its lease during commit")
        return True

    def _fail(self, lease: Lease, exc: BaseException) -> None:
        """Queue the job again after a backoff, or mark it failed on the last attempt.

        The backoff is `backoff * 2^(attempt-1)`, stored as `not_before`. The
        update is fenced the same way completion is, so a worker that lost its
        lease cannot reschedule someone else's claim.
        """
        message = str(exc)
        now = self.clock.now()
        with self._connect() as conn:
            with conn.transaction():
                locked = conn.execute(
                    "SELECT attempt FROM jobs WHERE id = %s AND lease_owner = %s "
                    "AND attempt = %s AND status = 'leased' FOR UPDATE",
                    (lease.job_id, lease.owner, lease.attempt),
                ).fetchone()
                if locked is None:
                    return
                if int(locked["attempt"]) >= self.max_attempts:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'failed', error = %s, not_before = NULL,
                            lease_owner = NULL, lease_expires = NULL, finished_at = %s
                        WHERE id = %s
                        """,
                        (message, now, lease.job_id),
                    )
                    return
                delay = self.backoff * (2 ** (int(locked["attempt"]) - 1))
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', error = %s, not_before = %s,
                        lease_owner = NULL, lease_expires = NULL, finished_at = NULL
                    WHERE id = %s
                    """,
                    (message, now + delay, lease.job_id),
                )

    def _job(self, job_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"job {job_id} is not in the queue")
        return row

    @contextmanager
    def _renewing(self, lease: Lease) -> Iterator[None]:
        """Keep the lease alive while the harness runs, then stop the renewal thread."""
        stop = threading.Event()

        def renew() -> None:
            interval = max(self.lease.total_seconds() / 3, 0.05)
            while not stop.wait(interval):
                expires = self.clock.now() + self.lease
                with self._connect() as conn:
                    with conn.transaction():
                        conn.execute(
                            """
                            UPDATE jobs SET lease_expires = %s
                            WHERE id = %s AND lease_owner = %s AND attempt = %s AND status = 'leased'
                            """,
                            (expires, lease.job_id, lease.owner, lease.attempt),
                        )

        thread = threading.Thread(target=renew, name=f"lease-{lease.job_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _ensure(conn: psycopg.Connection[dict[str, Any]]) -> None:
        conn.execute(_JOBS)
        conn.execute(_RESULTS)
        conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS not_before timestamptz")
        conn.execute("ALTER TABLE results ADD COLUMN IF NOT EXISTS intervals bytea")


def _identity(spec: PolicySpec, scenario: Scenario) -> dict[str, Any]:
    """Job-identity name and version, plus how the worker rebuilds the policy.

    An external policy's name and version come from its hello, so an edited
    policy does not reuse another version's results. The command stays on the
    job so the worker can start that process.
    """
    if spec.builtin is not None:
        name = builtin_policy(spec.builtin, spec.params, scenario).name
        version = spec.version
        builtin: str | None = spec.builtin
        command: str | None = None
        params = json.dumps(dict(spec.params))
    else:
        assert spec.command is not None
        name, version = _external_name(spec, scenario)
        builtin = None
        command = json.dumps(list(spec.command))
        params = json.dumps({})
    return {
        "policy_name": name,
        "policy_version": version,
        "builtin": builtin,
        "params": params,
        "command": command,
        "timeout_s": spec.timeout_s,
        "fallback": spec.fallback,
    }


def _external_name(spec: PolicySpec, scenario: Scenario) -> tuple[str, str]:
    assert spec.command is not None
    policy = ExternalPolicy(
        list(spec.command), scenario.products, timeout_s=spec.timeout_s, fallback=spec.fallback,
    )
    try:
        policy.start()
        if policy.version != spec.version:
            raise PolicyError(
                f"{policy.name} reports version {policy.version}, sweep says {spec.version}")
        return policy.name, policy.version
    finally:
        policy.close()


def _policy(job: Mapping[str, Any], scenario: Scenario) -> Any:
    builtin = job["builtin"]
    if isinstance(builtin, str):
        params = json.loads(job["params"])
        return builtin_policy(builtin, params, scenario)
    command = json.loads(job["command"])
    return ExternalPolicy(command, scenario.products, timeout_s=float(job["timeout_s"]),
                          fallback=str(job["fallback"]))


class _HeldPolicy:
    """An external policy whose process survives the harness's per-day reset.

    `run` closes the policy it was given and calls `begin_day` before the day.
    Closing here does nothing, and `begin_day` forgets the fallback without
    killing the child, so the next job of the same policy talks to the same
    process. A crash still starts a new one, inside the policy itself.
    """

    def __init__(self, inner: ExternalPolicy):
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def beliefs(self) -> dict[str, float]:
        return self._inner.beliefs

    @property
    def faults(self) -> FaultCounts:
        return self._inner.faults

    def begin_day(self) -> None:
        self._inner.begin_day(restart=False)

    def arm(self, streams: Any, day: Any) -> None:
        self._inner.arm(streams, day)

    def decide(self, observation: Observation) -> Capability:
        return self._inner.decide(observation)

    def close(self) -> None:
        return None


class _PolicyPool:
    """One external process per policy command, for the life of a worker."""

    def __init__(self) -> None:
        self._held: dict[tuple[tuple[str, ...], float, str], _HeldPolicy] = {}
        self._processes: list[ExternalPolicy] = []

    def policy(self, job: Mapping[str, Any], scenario: Scenario) -> Any:
        if isinstance(job["builtin"], str):
            return _policy(job, scenario)
        command = tuple(json.loads(job["command"]))
        key = (command, float(job["timeout_s"]), str(job["fallback"]))
        held = self._held.get(key)
        if held is None:
            inner = ExternalPolicy(
                list(command), scenario.products,
                timeout_s=float(job["timeout_s"]), fallback=str(job["fallback"]),
            )
            self._processes.append(inner)
            held = _HeldPolicy(inner)
            self._held[key] = held
        return held

    def close(self) -> None:
        processes, self._processes = self._processes, []
        self._held.clear()
        for process in processes:
            process.close()


def _scorecard(data: Mapping[str, Any]) -> Scorecard:
    days = tuple(
        DayResult(
            day=dt.date.fromisoformat(raw["day"]),
            products={name: ProductTotals(**totals) for name, totals in raw["products"].items()},
            faults=FaultCounts(**raw["faults"]),
            backup_floor_violations=raw["backup_floor_violations"],
            calm={name: ProductTotals(**totals) for name, totals in raw["calm"].items()},
            scarce={name: ProductTotals(**totals) for name, totals in raw["scarce"].items()},
            hourly_shortfall_mw={name: tuple(values) for name, values in raw["hourly_shortfall_mw"].items()},
        )
        for raw in data["days"]
    )
    beliefs = data.get("beliefs")
    return Scorecard(
        data["policy"], data["scenario"], data["seed"], data["fleet_case"],
        data["policy_view"], data["observed_case"], days,
        None if beliefs is None else {str(key): float(value) for key, value in beliefs.items()},
    )
