"""Play one operating day on a simulated 2-second clock.

The caller drives the clock. Each tick, one agent-host process per region
sends a heartbeat batch, and the coordinator process records reported
capability against the fleet's true deliverable MW.
"""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from harness.fleet import simulate_day
from harness.market import load_intervals
from harness.market.catalog import scarce_column
from harness.policy import Policy
from harness.products import PRODUCTS
from harness.replay.transport import Transport
from harness.replay.workers import run_coordinator, run_host
from harness.rng import RandomStreams
from harness.scenario import QUANTILE, Scenario

# The market-dataset loader contract: operating days [start, end] -> interval table.
MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]

TICK_S = 2.0
_REPLY_S = 30.0


class ReplayError(ValueError):
    """The replay could not be run."""


@dataclass(frozen=True)
class HostKill:
    """Stop the agent-host process for `region` at simulated time `at_s`.

    The host sends no further heartbeats. Its last telemetry counts until it
    is older than the scenario's stale threshold.
    """

    region: int
    at_s: float


@dataclass(frozen=True)
class TransportFaults:
    """Injected faults on heartbeats going to the coordinator.

    `delay_s` delivers each batch that many simulated seconds late. The
    telemetry still carries the time it was sent. `drop` is (region, time)
    pairs whose batch is discarded. `partition_regions` isolates those
    regions for the whole replay.
    """

    delay_s: float = 0.0
    drop: tuple[tuple[int, float], ...] = ()
    partition_regions: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Tick:
    """One simulated tick: what the policy reported, and what the fleet could deliver."""

    t_s: float
    reported_mw: dict[str, float]
    deliverable_mw: dict[str, float]


@dataclass(frozen=True)
class ReplayResult:
    day: dt.date
    seed: int
    tick_s: float
    ticks: tuple[Tick, ...]

    def write(self, path: Path) -> None:
        """Write the timeline next to anything else the caller asked for."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "day": self.day.isoformat(),
            "seed": self.seed,
            "tick_s": self.tick_s,
            "ticks": [
                {"t_s": tick.t_s, "reported_mw": tick.reported_mw, "deliverable_mw": tick.deliverable_mw}
                for tick in self.ticks
            ],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n")


def multiprocessing_available() -> bool:
    """False where a spawned coordinator and agent hosts cannot be started."""
    try:
        multiprocessing.get_context("spawn")
    except ValueError:
        return False
    return True


def replay(
    policy: Policy,
    scenario: Scenario,
    day: dt.date,
    *,
    seed: int | None = None,
    minutes: float | None = None,
    tick_s: float = TICK_S,
    speed: float | None = None,
    market: MarketLoader | None = None,
    out: Path | None = None,
    kills: Sequence[HostKill] = (),
    transport: TransportFaults | None = None,
) -> ReplayResult:
    """Replay `day` and return the timeline of reported capability versus true deliverable MW.

    `minutes` is simulated time from the start of the day; the default is the
    whole operating day. `speed` is simulated seconds per wall-clock second.
    The default spends no time waiting, so the clock runs as fast as the
    processes allow. `kills` stops agent-host processes. `transport` injects
    drop, delay, and partition. `out`, when given, is the timeline file.
    """
    if not multiprocessing_available():
        raise ReplayError("multiprocessing is not available")
    if tick_s <= 0:
        raise ReplayError(f"tick must be positive, got {tick_s}")
    if minutes is not None and minutes <= 0:
        raise ReplayError(f"minutes must be positive, got {minutes}")
    if speed is not None and speed <= 0:
        raise ReplayError(f"speed must be positive, got {speed}")
    seed = scenario.seed if seed is None else seed
    rows = (market or load_intervals)(day, day)
    if len(rows) == 0:
        raise ReplayError(f"no market intervals on {day}")
    span_s = _span_s(rows, minutes)
    case = _policy_case(scenario, simulate_day(
        scenario, day, pd.DatetimeIndex(rows.index), _stressed(rows), RandomStreams(scenario.name, seed)))
    result = ReplayResult(day, seed, tick_s, tuple(_play(
        policy, scenario, day, seed, rows, case, span_s, tick_s, speed, kills, transport)))
    if out is not None:
        result.write(out)
    return result


def _span_s(rows: pd.DataFrame, minutes: float | None) -> float:
    start = pd.Timestamp(rows.index[0])
    end = pd.Timestamp(rows.index[-1]) + pd.Timedelta(seconds=300)
    day_s = (end - start).total_seconds()
    if minutes is None:
        return day_s
    return min(day_s, minutes * 60.0)


def _policy_case(scenario: Scenario, cases: list[Any]):
    """The fleet the policy sees: the typical quantile, or the stochastic case."""
    if scenario.fleet.state == QUANTILE:
        return next(case for case in cases if case.name == scenario.fleet.quantile_mock.typical)
    return cases[0]


def _starts_ns(index: pd.Index) -> np.ndarray:
    """Nanoseconds since the epoch, one entry per interval start."""
    return np.array([pd.Timestamp(stamp).value for stamp in index], dtype=np.int64)


def _stressed(rows: pd.DataFrame) -> np.ndarray:
    scarce = [rows[scarce_column(suffix)].fillna(False).to_numpy(dtype=bool) for suffix in PRODUCTS.values()]
    return np.logical_or.reduce(scarce)


def _play(policy: Policy, scenario: Scenario, day: dt.date, seed: int, rows: pd.DataFrame,
          case: Any, span_s: float, tick_s: float, speed: float | None,
          kills: Sequence[HostKill], transport: TransportFaults | None) -> list[Tick]:
    ctx = multiprocessing.get_context("spawn")
    starts_ns = _starts_ns(rows.index)
    home_ids = [np.flatnonzero(np.arange(scenario.fleet.homes) % scenario.fleet.regions == region)
                for region in range(scenario.fleet.regions)]
    hosts: list[tuple[Any, Any]] = []
    coordinator: tuple[Any, Any] | None = None
    try:
        for region, ids in enumerate(home_ids):
            parent, child = ctx.Pipe(duplex=True)
            proc = ctx.Process(
                target=run_host,
                args=(region, child, ids, case.home_soc, case.home_online, case.home_backup, starts_ns),
                name=f"agent-host-{region}",
            )
            proc.start()
            child.close()
            hosts.append((proc, parent))
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=run_coordinator,
            args=(child, policy, scenario, rows, case.deliverable_mw, case.home_stale, starts_ns, seed, day),
            name="coordinator",
        )
        proc.start()
        child.close()
        coordinator = (proc, parent)
        return _clock(hosts, coordinator, span_s, tick_s, speed, kills, transport)
    finally:
        _stop(hosts, coordinator)


def _clock(hosts: list[tuple[Any, Any]], coordinator: tuple[Any, Any], span_s: float,
           tick_s: float, speed: float | None, kills: Sequence[HostKill],
           faults: TransportFaults | None) -> list[Tick]:
    coord_proc, coord = coordinator
    link = Transport(
        0.0 if faults is None else faults.delay_s,
        drop=() if faults is None else faults.drop,
        partition_regions=frozenset() if faults is None else faults.partition_regions,
    )
    dead: set[int] = set()
    t_s = 0.0
    while t_s < span_s - 1e-9:
        if speed is not None:
            time.sleep(tick_s / speed)
        for region, (proc, conn) in enumerate(hosts):
            if region in dead:
                continue
            if any(kill.region == region and t_s >= kill.at_s for kill in kills):
                _kill_host(proc)
                dead.add(region)
                continue
            conn.send(t_s)
            link.submit(_recv(conn, proc, f"agent host {region}"), t_s)
        coord.send((t_s, link.deliver(t_s)))
        t_s += tick_s
    for region, (proc, conn) in enumerate(hosts):
        if region not in dead:
            conn.send(None)
    coord.send(None)
    raw = _recv(coord, coord_proc, "coordinator")
    return [Tick(row["t_s"], dict(row["reported_mw"]), dict(row["deliverable_mw"])) for row in raw]


def _kill_host(proc: Any) -> None:
    if proc.is_alive():
        proc.kill()
    proc.join(timeout=2)


def _recv(conn: Any, proc: Any, who: str) -> Any:
    if not conn.poll(_REPLY_S):
        raise ReplayError(f"{who} sent nothing (exit {proc.exitcode})")
    try:
        return conn.recv()
    except (EOFError, OSError) as exc:
        raise ReplayError(f"{who} closed its pipe (exit {proc.exitcode})") from exc


def _stop(hosts: list[tuple[Any, Any]], coordinator: tuple[Any, Any] | None) -> None:
    for proc, conn in hosts:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=2)
        conn.close()
    if coordinator is None:
        return
    proc, conn = coordinator
    if proc.is_alive():
        proc.kill()
    proc.join(timeout=2)
    conn.close()
