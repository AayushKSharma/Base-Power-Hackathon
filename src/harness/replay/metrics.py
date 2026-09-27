"""Reaction latency and recovery for a live replay.

Reaction latency is the simulated time from a region going dark until that
region is absent from reported capability. A host kill stays visible until
telemetry is older than the stale threshold. An outage or a partition that
never delivers a heartbeat shows up on the tick it takes effect.

Recovery is the simulated time from losing at least 20% of agent hosts until
delivered MW is back on the commanded award. Percentiles are linear on the
sorted samples: one sample is itself, and the position between samples is
``(n - 1) * (q / 100)``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harness.products import PRODUCTS
from harness.replay.chaos import ChaosSchedule

# Delivered MW within this of commanded MW counts as back on target.
_ON_TARGET_MW = 1e-4


@dataclass(frozen=True)
class ReplayMetrics:
    reaction_p50_s: float
    reaction_p99_s: float
    recovery_p50_s: float
    recovery_p99_s: float
    floor_violations: int
    duplicate_commands: int
    dead_commands: int

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "reaction_p50_s": _json_float(self.reaction_p50_s),
            "reaction_p99_s": _json_float(self.reaction_p99_s),
            "recovery_p50_s": _json_float(self.recovery_p50_s),
            "recovery_p99_s": _json_float(self.recovery_p99_s),
            "floor_violations": self.floor_violations,
            "duplicate_commands": self.duplicate_commands,
            "dead_commands": self.dead_commands,
        }


def empty_metrics() -> ReplayMetrics:
    return ReplayMetrics(float("nan"), float("nan"), float("nan"), float("nan"), 0, 0, 0)


def measure(rows: Sequence[Mapping[str, Any]], chaos: ChaosSchedule | None, n_regions: int, *,
            floor_violations: int, duplicate_commands: int, dead_commands: int) -> ReplayMetrics:
    """p50/p99 reaction and recovery, plus the counters that must stay at zero."""
    reaction = _reaction(rows, chaos)
    recovery = _recovery(rows, chaos, n_regions)
    return ReplayMetrics(
        percentile(reaction, 50),
        percentile(reaction, 99),
        percentile(recovery, 50),
        percentile(recovery, 99),
        floor_violations,
        duplicate_commands,
        dead_commands,
    )


def percentile(samples: Sequence[float], q: float) -> float:
    """Linear percentile. An empty sample is NaN; a single sample is that value."""
    if not samples:
        return float("nan")
    ordered = sorted(float(sample) for sample in samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100.0)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _reaction(rows: Sequence[Mapping[str, Any]], chaos: ChaosSchedule | None) -> list[float]:
    if chaos is None:
        return []
    samples = []
    for region, at_s in _dark(chaos):
        reflected = _first(rows, at_s, lambda row: region in row["excluded_regions"])
        if reflected is None:
            continue
        samples.append(reflected - at_s)
    return samples


def _recovery(rows: Sequence[Mapping[str, Any]], chaos: ChaosSchedule | None,
              n_regions: int) -> list[float]:
    if chaos is None or n_regions <= 0:
        return []
    killed_at: dict[float, int] = {}
    for event in chaos.of("host_kill"):
        killed_at[event.at_s] = killed_at.get(event.at_s, 0) + 1
    samples = []
    for at_s, killed in killed_at.items():
        if killed / n_regions + 1e-12 < 0.2:
            continue
        reflected = _first(rows, at_s, _on_target)
        if reflected is None:
            continue
        samples.append(reflected - at_s)
    return samples


def _dark(chaos: ChaosSchedule) -> list[tuple[int, float]]:
    events: list[tuple[int, float]] = []
    for event in chaos.events:
        if event.kind == "host_kill" and event.region is not None:
            events.append((event.region, event.at_s))
        elif event.kind == "region_outage" and event.region is not None:
            events.append((event.region, event.at_s))
        elif event.kind == "partition":
            events.extend((region, event.at_s) for region in event.regions)
    return events


def _first(rows: Sequence[Mapping[str, Any]], at_s: float, ready: Any) -> float | None:
    for row in rows:
        if float(row["t_s"]) + 1e-9 < at_s:
            continue
        if ready(row):
            return float(row["t_s"])
    return None


def _on_target(row: Mapping[str, Any]) -> bool:
    commanded = row["commanded_mw"]
    delivered = row["delivered_mw"]
    return all(
        abs(float(delivered[product]) - float(commanded[product])) <= _ON_TARGET_MW
        for product in PRODUCTS
    )


def _json_float(value: float) -> float | None:
    if math.isnan(value):
        return None
    return value
