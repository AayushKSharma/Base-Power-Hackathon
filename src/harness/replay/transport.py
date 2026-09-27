"""Batches between agent hosts and the coordinator.

The transport can hold a batch, discard one, or isolate a region. A held
batch keeps the telemetry timestamp from when the host sent it.
"""

from __future__ import annotations

from typing import Any

import numpy as np


class Transport:
    """Drop, delay, and partition on the path to the coordinator.

    `drop_rate` is a percent in 0..100. Each batch draws once from `rng`, so
    the same seed and the same order of heartbeats discards the same ones.
    """

    def __init__(self, delay_s: float = 0.0, *,
                 drop: tuple[tuple[int, float], ...] = (),
                 partition_regions: frozenset[int] = frozenset(),
                 drop_rate: float = 0.0,
                 rng: np.random.Generator | None = None) -> None:
        if delay_s < 0:
            raise ValueError(f"delay must be non-negative, got {delay_s}")
        if not 0 <= drop_rate <= 100:
            raise ValueError(f"drop rate must be a percent from 0 to 100, got {drop_rate}")
        self.delay_s = delay_s
        self._drop = set(drop)
        self._partition = partition_regions
        self.drop_rate = drop_rate
        self._rng = rng
        self._held: list[tuple[float, dict[str, Any]]] = []

    def set_partition(self, regions: frozenset[int]) -> None:
        self._partition = regions

    def submit(self, batch: dict[str, Any], t_s: float) -> None:
        region = int(batch["region"])
        if region in self._partition or (region, t_s) in self._drop:
            return
        if self.drop_rate > 0 and self._rng is not None and self._rng.random() < self.drop_rate / 100.0:
            return
        self._held.append((t_s + self.delay_s, batch))

    def deliver(self, t_s: float) -> list[dict[str, Any]]:
        ready = [batch for at, batch in self._held if at <= t_s + 1e-9]
        self._held = [(at, batch) for at, batch in self._held if at > t_s + 1e-9]
        return ready


class CommandTransport:
    """Duplicate or reorder commands on the way to the agent hosts.

    Reorder delivers this tick's commands first, then the previous tick's, so
    an older version arrives after a newer one.
    """

    def __init__(self, *, duplicate: bool = False, reorder: bool = False) -> None:
        self.duplicate = duplicate
        self.reorder = reorder
        self._previous: list[dict[str, Any]] = []

    def deliver(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        burst = list(commands)
        if self.duplicate:
            burst = burst + list(commands)
        if not self.reorder:
            return burst
        out = list(reversed(burst)) + self._previous
        self._previous = burst
        return out
