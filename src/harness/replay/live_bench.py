"""Live-replay benchmark: tick rate and latency at 10k and 50k agents.

An agent is a home. Ten agent-host processes share the fleet, so 20% of the
agents is two hosts killed at 2 simulated seconds. The run lasts long enough
for the 180 s stale rule to drop those hosts from reported capability, unless
the wall-clock budget runs out first. A partial run reports the tick rate it
actually achieved and leaves the latencies blank.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.policy import ConstantHaircut
from harness.replay.chaos import ChaosEvent, ChaosSchedule
from harness.replay.metrics import ReplayMetrics, empty_metrics
from harness.replay.session import MarketLoader, ReplayResult, replay
from harness.scenario import Scenario, parse_scenario

REGIONS = 10
# Through the tick at 182 s, where a host killed at 2 s goes stale.
BENCH_MINUTES = 184 / 60
# Wall-clock caps so a slow fleet is recorded instead of hanging.
BUDGET_S = {10_000: 600.0, 50_000: 240.0}
DEFAULT_AGENTS = (10_000, 50_000)


@dataclass(frozen=True)
class LiveBenchRow:
    agents: int
    finished: bool
    ticks: int
    elapsed_s: float
    clock_s: float | None
    tick_rate: float | None
    metrics: ReplayMetrics
    budget_s: float
    note: str


def budget_for(agents: int) -> float:
    """Seconds allowed for this fleet. Larger fleets get a shorter cap."""
    if agents in BUDGET_S:
        return BUDGET_S[agents]
    return 600.0 if agents < 50_000 else 240.0


def run_live_bench(market: MarketLoader, day: dt.date, agents: tuple[int, ...] = DEFAULT_AGENTS, *,
                   seed: int = 7, budget: float | None = None) -> str:
    """Replay each fleet size and return the markdown table."""
    rows = [
        _bounded(market, day, count, seed, budget if budget is not None else budget_for(count))
        for count in agents
    ]
    return render_live(rows, day, seed)


def _bounded(market: MarketLoader, day: dt.date, agents: int, seed: int, budget_s: float) -> LiveBenchRow:
    """Stop a fleet that has not returned by the budget. Do not invent its tick rate."""
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    queue: Any = ctx.Queue()
    proc = ctx.Process(
        target=_one_to_queue, args=(queue, market, day, agents, seed, budget_s),
        name=f"bench-live-{agents}",
    )
    proc.start()
    proc.join(budget_s + 30)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
        return _unfinished(agents, budget_s, f"stopped at the {budget_s:.0f}s wall budget before the replay returned")
    if proc.exitcode != 0:
        return _unfinished(agents, budget_s, f"replay process exited {proc.exitcode}")
    return queue.get()


def _unfinished(agents: int, budget_s: float, note: str) -> LiveBenchRow:
    return LiveBenchRow(agents, False, 0, budget_s, None, None, empty_metrics(), budget_s, note)


def _one_to_queue(queue: Any, market: MarketLoader, day: dt.date, agents: int, seed: int,
                  budget_s: float) -> None:
    queue.put(_one(market, day, agents, seed, budget_s))


def render_live(rows: tuple[LiveBenchRow, ...] | list[LiveBenchRow], day: dt.date, seed: int) -> str:
    """The saved benchmark. Rates and latencies are the measured ones."""
    lines = [
        "# Live replay benchmark",
        "",
        "An agent is a home. Ten agent-host processes share the fleet. "
        "The clock steps 2 simulated seconds. At 2 s the schedule kills 20% of the hosts. "
        f"The operating day is {day.isoformat()}, seed {seed}. "
        "Reaction latency is simulated time until reported capability no longer includes "
        "those hosts (the 180 s stale rule). Recovery is simulated time until delivered MW "
        "is back on the commanded award. The tick rate is completed ticks divided by time "
        "inside the replay clock, after the fleet is built. Wall time includes that setup. "
        "A run that does not return within its wall-clock budget has no tick rate.",
        "",
        "| Agents | Finished | Ticks | Wall (s) | Clock (s) | Ticks/s | Reaction p50 (s) | Reaction p99 (s) | Recovery p50 (s) | Recovery p99 (s) | Note |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        rate = "—" if row.tick_rate is None else f"{row.tick_rate:.3f}"
        clock = "—" if row.clock_s is None else f"{row.clock_s:.2f}"
        metrics = row.metrics
        lines.append(
            f"| {row.agents} | {'yes' if row.finished else 'no'} | {row.ticks} | "
            f"{row.elapsed_s:.1f} | {clock} | {rate} | {_cell(metrics.reaction_p50_s)} | "
            f"{_cell(metrics.reaction_p99_s)} | {_cell(metrics.recovery_p50_s)} | "
            f"{_cell(metrics.recovery_p99_s)} | {row.note} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_live(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n")
    return path


def _one(market: MarketLoader, day: dt.date, agents: int, seed: int, budget_s: float) -> LiveBenchRow:
    regions = min(REGIONS, agents)
    killed = max(1, int(round(regions * 0.2)))
    schedule = ChaosSchedule(tuple(
        ChaosEvent("host_kill", 2.0, region=region) for region in range(killed)
    ))
    started = time.perf_counter()
    result = replay(
        ConstantHaircut(0.5), _scenario(agents, regions, day), day, seed=seed,
        minutes=BENCH_MINUTES, market=market, chaos=schedule, wall_s=budget_s,
    )
    elapsed = time.perf_counter() - started
    rate = (len(result.ticks) / result.clock_s) if result.clock_s > 0 and result.ticks else None
    return LiveBenchRow(
        agents, not result.incomplete, len(result.ticks), elapsed, result.clock_s, rate, result.metrics,
        budget_s, _note(result, killed, regions, budget_s),
    )


def _note(result: ReplayResult, killed: int, regions: int, budget_s: float) -> str:
    loss = f"killed {killed} of {regions} hosts"
    if result.incomplete:
        return f"{loss}; stopped at the {budget_s:.0f}s wall budget"
    return loss


def _scenario(agents: int, regions: int, day: dt.date) -> Scenario:
    start = f"{day.isoformat()} 00:00"
    return parse_scenario({
        "seed": 7,
        "fleet": {
            "homes": agents, "regions": regions, "battery_kwh": 20, "inverter_kw": 10,
            "backup_floor": 0.2, "soc": {"fixed": 0.6}, "telemetry_stale_s": 180,
            "state": "quantile",
            "quantile_mock": {
                "shares": {"P10": 1, "P25": 1, "P50": 1, "P75": 1, "P90": 1},
                "policy_view": "typical", "typical": "P50",
            },
        },
        "failures": {
            "home_dropout_per_h": 0, "home_dropout_min": 60, "region_outage_per_h": 0,
            "region_outage_min": 120, "scarcity_stress": 1, "forced_region_outages": [],
        },
        "deployments": {
            "calm": 0, "scarce": 0, "refill_kw": 0,
            "forced": [{"product": "ECRS", "start": start, "minutes": 5}],
        },
        "scoring": {
            "preset": "energy", "load_zone": "HOUSTON", "compliance_per_mw": 0, "spd_per_mwh": 0,
            "exceedance_mw": [0, 1], "tolerance_mw": [1],
        },
        "products": {
            "ECRS": {"duration_h": 1, "cap_mw": 100, "cap_share": 0.9},
            "NONSPIN": {"duration_h": 4, "cap_mw": 100, "cap_share": 0.9},
        },
    }, name="bench-live")


def _cell(value: float) -> str:
    if math.isnan(value):
        return "—"
    return f"{value:.1f}"
