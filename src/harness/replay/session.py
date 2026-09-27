"""Play one operating day on a simulated 2-second clock.

The caller drives the clock. Each tick, one agent-host process per region
sends a heartbeat batch, and the coordinator process records reported
capability against the fleet's true deliverable MW. When the day's
deployments call for power, the coordinator commands a share of that award
and records the MW the hosts actually discharge.
"""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from harness.fleet import simulate_day
from harness.market import load_intervals
from harness.market.catalog import scarce_column
from harness.policy import Policy
from harness.products import PRODUCTS
from harness.replay.chaos import ChaosEvent, ChaosSchedule
from harness.replay.metrics import ReplayMetrics, empty_metrics, measure
from harness.replay.transport import CommandTransport, Transport
from harness.replay.workers import region_deliverable_mw, run_coordinator, run_host
from harness.rng import RandomStreams
from harness.runner import _deployments
from harness.scenario import QUANTILE, Scenario

# The market-dataset loader contract: operating days [start, end] -> interval table.
MarketLoader = Callable[[dt.date, dt.date], pd.DataFrame]

TICK_S = 2.0
_REPLY_S = 30.0


class ReplayError(ValueError):
    """The replay could not be run."""


@dataclass(frozen=True)
class CommandFaults:
    """Deliver each command twice, and deliver the previous tick's commands after.

    Agents that apply only a newer version discharge the setpoint once.
    """

    duplicate: bool = False
    reorder: bool = False


@dataclass(frozen=True)
class CoordinatorKill:
    """Stop the coordinator at simulated time `at_s` and start a new one.

    The new process rebuilds from Postgres and hears an agent only after that
    agent heartbeats to it.
    """

    at_s: float


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
    """One simulated tick: reported capability, true deliverable, and the deployment.

    `commanded_mw` is the award being asked for while a product is deployed.
    `delivered_mw` is what the agent hosts discharged on that tick.
    """

    t_s: float
    reported_mw: dict[str, float]
    deliverable_mw: dict[str, float]
    commanded_mw: dict[str, float]
    delivered_mw: dict[str, float]


@dataclass(frozen=True)
class ReplayResult:
    day: dt.date
    seed: int
    tick_s: float
    ticks: tuple[Tick, ...]
    double_discharges: int = 0
    floor_breaches: int = 0
    coordinator_restarts: int = 0
    dead_commands: int = 0
    metrics: ReplayMetrics = field(default_factory=empty_metrics)
    events: tuple[ChaosEvent, ...] = ()
    incomplete: bool = False
    clock_s: float = 0.0

    def write(self, path: Path) -> None:
        """Write the timeline next to anything else the caller asked for."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "day": self.day.isoformat(),
            "seed": self.seed,
            "tick_s": self.tick_s,
            "double_discharges": self.double_discharges,
            "floor_breaches": self.floor_breaches,
            "coordinator_restarts": self.coordinator_restarts,
            "dead_commands": self.dead_commands,
            "metrics": self.metrics.as_dict(),
            "events": [_event_payload(event) for event in self.events],
            "incomplete": self.incomplete,
            "ticks": [
                {
                    "t_s": tick.t_s,
                    "reported_mw": tick.reported_mw,
                    "deliverable_mw": tick.deliverable_mw,
                    "commanded_mw": tick.commanded_mw,
                    "delivered_mw": tick.delivered_mw,
                }
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
    commands: CommandFaults | None = None,
    coordinator_kills: Sequence[CoordinatorKill] = (),
    dsn: str | None = None,
    chaos: ChaosSchedule | None = None,
    chart: Path | None = None,
    wall_s: float | None = None,
) -> ReplayResult:
    """Replay `day` and return the timeline of reported capability versus true deliverable MW.

    `minutes` is simulated time from the start of the day; the default is the
    whole operating day. `speed` is simulated seconds per wall-clock second.
    The default spends no time waiting, so the clock runs as fast as the
    processes allow. `kills` stops agent-host processes. `transport` injects
    drop, delay, and partition on heartbeats.     `commands` duplicates or reorders
    the allocation commands. `coordinator_kills` restarts the coordinator from
    `dsn`. `chaos` injects the schedule's failures at their simulated times.
    `out`, when given, is the timeline file. `chart`, when given, is the timeline chart.
    `wall_s`, when given, is a wall-clock budget for fleet setup and the clock.
    Past the budget the result is incomplete, and setup that already used the
    budget does not start the processes.
    """
    if not multiprocessing_available():
        raise ReplayError("multiprocessing is not available")
    if tick_s <= 0:
        raise ReplayError(f"tick must be positive, got {tick_s}")
    if minutes is not None and minutes <= 0:
        raise ReplayError(f"minutes must be positive, got {minutes}")
    if speed is not None and speed <= 0:
        raise ReplayError(f"speed must be positive, got {speed}")
    if wall_s is not None and wall_s <= 0:
        raise ReplayError(f"wall_s must be positive, got {wall_s}")
    opened = time.perf_counter()
    seed = scenario.seed if seed is None else seed
    rows = (market or load_intervals)(day, day)
    if len(rows) == 0:
        raise ReplayError(f"no market intervals on {day}")
    span_s = _span_s(rows, minutes)
    streams = RandomStreams(scenario.name, seed)
    case = _policy_case(scenario, simulate_day(
        scenario, day, pd.DatetimeIndex(rows.index), _stressed(rows), streams))
    deployed = _deployments(scenario, day, rows, streams)
    remaining = wall_s
    if wall_s is not None:
        remaining = wall_s - (time.perf_counter() - opened)
        if remaining <= 0:
            metrics = measure(
                [], chaos, scenario.fleet.regions, floor_violations=0,
                duplicate_commands=0, dead_commands=0)
            return ReplayResult(
                day, seed, tick_s, (), 0, 0, 0, 0, metrics,
                () if chaos is None else chaos.events, True, 0.0)
    ticks, double_discharges, floor_breaches, coordinator_restarts, dead_commands, metrics, incomplete, clock_s = _play(
        policy, scenario, day, seed, rows, case, span_s, tick_s, speed, kills, transport,
        deployed, commands, coordinator_kills, dsn, chaos, remaining)
    result = ReplayResult(
        day, seed, tick_s, tuple(ticks), double_discharges, floor_breaches, coordinator_restarts,
        dead_commands, metrics, () if chaos is None else chaos.events, incomplete, clock_s)
    if out is not None:
        result.write(out)
    if chart is not None:
        from harness.replay.chart import write_timeline_chart
        write_timeline_chart(result, chart)
    return result


def _event_payload(event: ChaosEvent) -> dict[str, object]:
    payload: dict[str, object] = {"type": event.kind, "at_s": event.at_s}
    if event.region is not None:
        payload["region"] = event.region
    if event.regions:
        payload["regions"] = list(event.regions)
    if event.kind == "drop":
        payload["percent"] = event.percent
    if event.kind == "telemetry_delay":
        payload["delay_s"] = event.delay_s
    if event.until_s is not None:
        payload["until_s"] = event.until_s
    return payload


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
          kills: Sequence[HostKill], transport: TransportFaults | None,
          deployed: dict[str, Any], commands: CommandFaults | None,
          coordinator_kills: Sequence[CoordinatorKill], dsn: str | None,
          chaos: ChaosSchedule | None, wall_s: float | None,
          ) -> tuple[list[Tick], int, int, int, int, ReplayMetrics, bool, float]:
    ctx = multiprocessing.get_context("spawn")
    starts_ns = _starts_ns(rows.index)
    home_ids = [np.flatnonzero(np.arange(scenario.fleet.homes) % scenario.fleet.regions == region)
                for region in range(scenario.fleet.regions)]
    region_mw = region_deliverable_mw(scenario, case)
    hosts: list[tuple[Any, Any]] = []
    current: list[tuple[Any, Any] | None] = [None]

    def start_coordinator() -> tuple[Any, Any]:
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=run_coordinator,
            args=(
                child, policy, scenario, rows, case.deliverable_mw, case.home_stale, starts_ns,
                seed, day, deployed, dsn, region_mw,
            ),
            name="coordinator",
        )
        proc.start()
        child.close()
        started = (proc, parent)
        current[0] = started
        return started

    try:
        for region, ids in enumerate(home_ids):
            parent, child = ctx.Pipe(duplex=True)
            fleet = scenario.fleet
            proc = ctx.Process(
                target=run_host,
                args=(
                    region, child, ids, case.home_soc, case.home_online, case.home_backup, starts_ns,
                    fleet.battery_kwh, fleet.inverter_kw, fleet.backup_floor,
                ),
                name=f"agent-host-{region}",
            )
            proc.start()
            child.close()
            hosts.append((proc, parent))
        return _clock(
            hosts, start_coordinator, span_s, tick_s, speed, kills, transport, commands,
            coordinator_kills, chaos, seed, scenario.fleet.regions, wall_s,
        )
    finally:
        _stop(hosts, current[0])


def _clock(hosts: list[tuple[Any, Any]], start_coordinator: Callable[[], tuple[Any, Any]],
           span_s: float, tick_s: float, speed: float | None, kills: Sequence[HostKill],
           faults: TransportFaults | None, command_faults: CommandFaults | None,
           coordinator_kills: Sequence[CoordinatorKill], chaos: ChaosSchedule | None,
           seed: int, n_regions: int, wall_s: float | None,
           ) -> tuple[list[Tick], int, int, int, int, ReplayMetrics, bool, float]:
    coord_proc, coord = start_coordinator()
    link = Transport(
        0.0 if faults is None else faults.delay_s,
        drop=() if faults is None else faults.drop,
        partition_regions=frozenset() if faults is None else faults.partition_regions,
        rng=np.random.default_rng(seed),
    )
    orders = CommandTransport(
        duplicate=False if command_faults is None else command_faults.duplicate,
        reorder=False if command_faults is None else command_faults.reorder,
    )
    kills = tuple(kills) + _host_kills(chaos)
    coordinator_kills = tuple(coordinator_kills) + _coordinator_kills(chaos)
    dead: set[int] = set()
    restarted: set[float] = set()
    kept: list[dict[str, Any]] = []
    kept_doubles = 0
    kept_floors = 0
    restarts = 0
    dead_commands = 0
    incomplete = False
    started = time.perf_counter()
    t_s = 0.0
    while t_s < span_s - 1e-9:
        if wall_s is not None and time.perf_counter() - started >= wall_s:
            incomplete = True
            break
        if any(kill.at_s <= t_s and kill.at_s not in restarted for kill in coordinator_kills):
            coord.send(("flush",))
            flushed = _recv(coord, coord_proc, "coordinator")
            kept.extend(flushed["ticks"])
            kept_doubles += int(flushed["double_discharges"])
            kept_floors += int(flushed["floor_breaches"])
            _kill_host(coord_proc)
            coord.close()
            for kill in coordinator_kills:
                if kill.at_s <= t_s:
                    restarted.add(kill.at_s)
            coord_proc, coord = start_coordinator()
            restarts += 1
        if speed is not None:
            time.sleep(tick_s / speed)
        link.drop_rate = _drop_percent(chaos, t_s)
        link.delay_s = _telemetry_delay(faults, chaos, t_s)
        link.set_partition(_partition_regions(faults, chaos, t_s))
        outaged = _outaged_regions(chaos, t_s)
        for region, (proc, conn) in enumerate(hosts):
            if region in dead:
                continue
            if any(kill.region == region and t_s >= kill.at_s for kill in kills):
                _kill_host(proc)
                dead.add(region)
                continue
            conn.send(("sample", t_s, region in outaged))
            link.submit(_recv(conn, proc, f"agent host {region}"), t_s)
        coord.send(("tick", t_s, link.deliver(t_s), sorted(outaged)))
        commands = orders.deliver(_recv(coord, coord_proc, "coordinator"))
        by_agent: dict[int, list[Any]] = {}
        for command in commands:
            agent = int(command["agent_id"])
            if agent in dead:
                dead_commands += 1
                continue
            by_agent.setdefault(agent, []).append(command)
        reports = []
        for region, (proc, conn) in enumerate(hosts):
            if region in dead:
                continue
            conn.send(("apply", t_s, by_agent.get(region, []), tick_s))
            reports.append(_recv(conn, proc, f"agent host {region}"))
        coord.send(("applied", reports))
        t_s += tick_s
    for region, (proc, conn) in enumerate(hosts):
        if region not in dead:
            conn.send(None)
    coord.send(None)
    raw = _recv(coord, coord_proc, "coordinator")
    rows = kept + raw["ticks"]
    doubles = kept_doubles + int(raw["double_discharges"])
    floors = kept_floors + int(raw["floor_breaches"])
    ticks = [Tick(
        row["t_s"], dict(row["reported_mw"]), dict(row["deliverable_mw"]),
        dict(row["commanded_mw"]), dict(row["delivered_mw"]),
    ) for row in rows]
    return ticks, doubles, floors, restarts, dead_commands, measure(
        rows, chaos, n_regions, floor_violations=floors, duplicate_commands=doubles,
        dead_commands=dead_commands), incomplete, time.perf_counter() - started


def _coordinator_kills(chaos: ChaosSchedule | None) -> tuple[CoordinatorKill, ...]:
    if chaos is None:
        return ()
    return tuple(CoordinatorKill(at_s=event.at_s) for event in chaos.of("coordinator_restart"))


def _outaged_regions(chaos: ChaosSchedule | None, t_s: float) -> set[int]:
    if chaos is None:
        return set()
    return {
        event.region
        for event in chaos.covering("region_outage", t_s)
        if event.region is not None
    }


def _partition_regions(faults: TransportFaults | None, chaos: ChaosSchedule | None,
                       t_s: float) -> frozenset[int]:
    regions = set() if faults is None else set(faults.partition_regions)
    if chaos is not None:
        for event in chaos.covering("partition", t_s):
            regions.update(event.regions)
    return frozenset(regions)


def _telemetry_delay(faults: TransportFaults | None, chaos: ChaosSchedule | None, t_s: float) -> float:
    base = 0.0 if faults is None else faults.delay_s
    if chaos is None:
        return base
    active = chaos.covering("telemetry_delay", t_s)
    if not active:
        return base
    return max(event.delay_s for event in active)


def _host_kills(chaos: ChaosSchedule | None) -> tuple[HostKill, ...]:
    if chaos is None:
        return ()
    return tuple(
        HostKill(region=event.region, at_s=event.at_s)
        for event in chaos.of("host_kill")
        if event.region is not None
    )


def _drop_percent(chaos: ChaosSchedule | None, t_s: float) -> float:
    if chaos is None:
        return 0.0
    active = chaos.covering("drop", t_s)
    if not active:
        return 0.0
    return max(event.percent for event in active)


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
