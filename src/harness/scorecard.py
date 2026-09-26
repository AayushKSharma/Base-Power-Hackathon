"""Scorecard: what a policy earned over a run, kept as per-day sums and counts."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class ProductTotals:
    """Sums and counts for one product. Never rates, so days combine exactly."""

    intervals: int = 0  # five-minute intervals in the run
    skipped: int = 0  # intervals not scored: settlement price missing (bad data)
    # The sums below cover scored intervals only.
    reported_mw_h: float = 0.0  # reported capability x hours
    award_mw_h: float = 0.0  # awarded MW x hours
    revenue: float = 0.0  # $, award x 15-minute RT MCPC x interval length

    def __add__(self, other: ProductTotals) -> ProductTotals:
        return ProductTotals(**{f.name: getattr(self, f.name) + getattr(other, f.name)
                                for f in fields(self)})


@dataclass(frozen=True)
class FaultCounts:
    """How often an external policy missed a decision. Stored as counts, like everything else."""

    timeouts: int = 0
    malformed: int = 0
    restarts: int = 0
    fallbacks: int = 0

    def __add__(self, other: FaultCounts) -> FaultCounts:
        return FaultCounts(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    def __sub__(self, other: FaultCounts) -> FaultCounts:
        return FaultCounts(**{f.name: getattr(self, f.name) - getattr(other, f.name) for f in fields(self)})


@dataclass(frozen=True)
class DayResult:
    day: dt.date
    products: dict[str, ProductTotals]
    faults: FaultCounts = FaultCounts()


@dataclass(frozen=True)
class Scorecard:
    """One policy on one scenario and seed, over a run of operating days.

    Only per-day sums and counts are stored; totals are summed on demand, so a
    range scored day by day (say, on the run farm) combines to exactly the
    same scorecard as a single run over the range.
    """

    policy: str
    scenario: str
    seed: int
    days: tuple[DayResult, ...]

    @staticmethod
    def combine(cards: Iterable[Scorecard]) -> Scorecard:
        """Merge scorecards of the same policy, scenario and seed over disjoint days."""
        cards = list(cards)
        labels = {(c.policy, c.scenario, c.seed) for c in cards}
        if len(labels) != 1:
            raise ValueError(f"can only combine scorecards of one policy, scenario and seed; got {sorted(labels)}")
        days = sorted((d for c in cards for d in c.days), key=lambda d: d.day)
        repeated = sorted({a.day for a, b in zip(days, days[1:]) if a.day == b.day})
        if repeated:
            raise ValueError(f"days scored more than once: {', '.join(map(str, repeated))}")
        policy, scenario, seed = labels.pop()
        return Scorecard(policy, scenario, seed, tuple(days))

    @property
    def start(self) -> dt.date:
        return self.days[0].day

    @property
    def end(self) -> dt.date:
        return self.days[-1].day

    @property
    def totals(self) -> dict[str, ProductTotals]:
        out: dict[str, ProductTotals] = {}
        for day in self.days:
            for product, totals in day.products.items():
                out[product] = out.get(product, ProductTotals()) + totals
        return out

    @property
    def faults(self) -> FaultCounts:
        total = FaultCounts()
        for day in self.days:
            total += day.faults
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "scenario": self.scenario,
            "seed": self.seed,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "totals": {p: asdict(t) for p, t in self.totals.items()},
            "faults": asdict(self.faults),
            "days": [{"day": d.day.isoformat(),
                      "products": {p: asdict(t) for p, t in d.products.items()},
                      "faults": asdict(d.faults)}
                     for d in self.days],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_table(self) -> str:
        """The scorecard as a terminal table."""
        n = len(self.days)
        lines = [
            f"{self.policy} | scenario {self.scenario} | seed {self.seed}",
            f"{self.start} to {self.end} ({n} day{'s' if n != 1 else ''})",
            (f"timeouts {self.faults.timeouts}  malformed {self.faults.malformed}"
             f"  restarts {self.faults.restarts}  fallbacks {self.faults.fallbacks}"),
            "",
        ]
        header = ("Product", "Intervals", "Skipped", "Reported MW-h", "Awarded MW-h", "Revenue $")
        rows = [(p, f"{t.intervals:,}", f"{t.skipped:,}", f"{t.reported_mw_h:,.1f}",
                 f"{t.award_mw_h:,.1f}", f"{t.revenue:,.2f}") for p, t in self.totals.items()]
        rows.append(("Total", "", "", "", "", f"{sum(t.revenue for t in self.totals.values()):,.2f}"))
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            lines.append("  ".join(cell.ljust(w) if i == 0 else cell.rjust(w)
                                   for i, (cell, w) in enumerate(zip(row, widths))))
        return "\n".join(lines) + "\n"
