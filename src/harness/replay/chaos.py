"""A chaos schedule: the same failures at the same simulated times every run.

Drop percentage draws from the replay seed, so two runs with that seed discard
the same heartbeats.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# One file entry per failure the live replay can inject.
EVENT_TYPES = (
    "host_kill",
    "drop",
    "telemetry_delay",
    "partition",
    "region_outage",
    "coordinator_restart",
)


class ChaosError(ValueError):
    """The chaos schedule could not be loaded."""


@dataclass(frozen=True)
class ChaosEvent:
    """One injected failure.

    `kind` is an EVENT_TYPES name. A windowed event applies while
    `at_s <= t < until_s`. `until_s` of None means through the end of the run.
    `host_kill` and `coordinator_restart` happen at `at_s` and are not windows.
    """

    kind: str
    at_s: float
    region: int | None = None
    regions: tuple[int, ...] = ()
    percent: float = 0.0
    delay_s: float = 0.0
    until_s: float | None = None

    def covers(self, t_s: float) -> bool:
        if t_s + 1e-9 < self.at_s:
            return False
        return self.until_s is None or t_s < self.until_s - 1e-9


@dataclass(frozen=True)
class ChaosSchedule:
    events: tuple[ChaosEvent, ...]

    def of(self, kind: str) -> tuple[ChaosEvent, ...]:
        return tuple(event for event in self.events if event.kind == kind)

    def covering(self, kind: str, t_s: float) -> tuple[ChaosEvent, ...]:
        return tuple(event for event in self.of(kind) if event.covers(t_s))


def load_chaos(path: Path) -> ChaosSchedule:
    """Load a chaos schedule YAML file."""
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except OSError as exc:
        raise ChaosError(str(exc)) from exc
    if not isinstance(raw, dict) or set(raw) != {"events"}:
        raise ChaosError("chaos schedule must contain only 'events'")
    rows = raw["events"]
    if not isinstance(rows, list) or not rows:
        raise ChaosError("chaos schedule events must be a non-empty list")
    return ChaosSchedule(tuple(_event(item, index) for index, item in enumerate(rows)))


def _event(item: Any, index: int) -> ChaosEvent:
    where = f"events[{index}]"
    if not isinstance(item, dict):
        raise ChaosError(f"{where}: expected a mapping")
    kind = item.get("type")
    if kind not in EVENT_TYPES:
        raise ChaosError(f"{where}.type: expected one of {', '.join(EVENT_TYPES)}, got {kind!r}")
    allowed = {"type", "at_s", "until_s"}
    if kind == "drop":
        allowed |= {"percent"}
    elif kind == "telemetry_delay":
        allowed |= {"delay_s"}
    elif kind == "partition":
        allowed |= {"regions"}
    elif kind in {"host_kill", "region_outage"}:
        allowed |= {"region"}
    extra = set(item) - allowed
    if extra:
        raise ChaosError(f"{where}: unknown fields {sorted(extra)}")
    at_s = _number(item.get("at_s"), f"{where}.at_s", low=0)
    until = None if "until_s" not in item or item["until_s"] is None else _number(
        item["until_s"], f"{where}.until_s", low=0)
    if until is not None and until <= at_s:
        raise ChaosError(f"{where}.until_s: must be after at_s {at_s}, got {until}")
    region = None
    regions: tuple[int, ...] = ()
    percent = 0.0
    delay_s = 0.0
    if kind == "drop":
        percent = _number(item.get("percent"), f"{where}.percent", low=0, high=100)
    elif kind == "telemetry_delay":
        delay_s = _number(item.get("delay_s"), f"{where}.delay_s", low=0)
    elif kind == "partition":
        regions = _regions(item.get("regions"), f"{where}.regions")
    elif kind in {"host_kill", "region_outage"}:
        region = _integer(item.get("region"), f"{where}.region")
    return ChaosEvent(kind, at_s, region, regions, percent, delay_s, until)


def _number(value: Any, where: str, *, low: float, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChaosError(f"{where}: expected a number, got {value!r}")
    number = float(value)
    if number < low or (high is not None and number > high):
        raise ChaosError(f"{where}: out of range, got {number}")
    return number


def _integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChaosError(f"{where}: expected an integer, got {value!r}")
    if value < 0:
        raise ChaosError(f"{where}: must be non-negative, got {value}")
    return value


def _regions(value: Any, where: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ChaosError(f"{where}: expected a non-empty list")
    return tuple(_integer(item, f"{where}[{index}]") for index, item in enumerate(value))
