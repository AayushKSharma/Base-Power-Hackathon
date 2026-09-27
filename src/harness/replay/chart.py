"""Timeline chart for one live replay: capability, deliverable, command, delivery."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from harness.replay.session import ReplayResult

_SERIES = (
    ("reported_mw", "reported capability"),
    ("deliverable_mw", "true deliverable"),
    ("commanded_mw", "commanded"),
    ("delivered_mw", "delivered"),
)


def write_timeline_chart(result: ReplayResult, path: Path) -> None:
    """Plot total MW and mark each chaos event at its simulated time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    times = [tick.t_s for tick in result.ticks]
    figure, axes = plt.subplots(figsize=(8.0, 4.2))
    for attr, label in _SERIES:
        axes.plot(times, [_total(tick, attr) for tick in result.ticks], label=label)
    seen: set[str] = set()
    for event in result.events:
        mark = event.kind if event.kind not in seen else None
        seen.add(event.kind)
        axes.axvline(event.at_s, color="0.35", linestyle="--", linewidth=0.8, label=mark)
    axes.set_xlabel("Simulated time (s)")
    axes.set_ylabel("MW")
    axes.set_title("Live replay")
    axes.legend(fontsize=8, loc="best")
    figure.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(figure)


def _total(tick: object, attr: str) -> float:
    values = getattr(tick, attr)
    return float(sum(values.values()))
