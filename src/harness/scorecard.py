"""Scorecard: what a policy earned and risked over a run, kept as per-day sums and counts."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class ProductTotals:
    """Sums and counts for one product. Never rates, so days combine exactly.

    K is the reported capability, D the true deliverable MW, and an award
    min(MW, cap x cap share).
    """

    intervals: int = 0  # five-minute intervals in the run
    skipped: int = 0  # intervals left out of the $ sums: settlement price missing (bad data)
    # Physical exposure, over every interval.
    reported_mw_h: float = 0.0  # K x hours
    award_mw_h: float = 0.0  # award of K x hours
    deliverable_mw_h: float = 0.0  # D x hours
    oversold_mw_h: float = 0.0  # max(K - D, 0) x hours
    undersold_mw_h: float = 0.0  # max(D - K, 0) x hours
    overstated: int = 0  # intervals where K > D
    # Dollars, over priced intervals only.
    revenue: float = 0.0  # award x 15-minute RT MCPC x interval length
    # Regret against a hindsight oracle that reports exactly D: what the oracle's
    # larger award would have earned, max(award of D - award of K, 0) x MCPC x hours.
    revenue_given_up: float = 0.0

    def __add__(self, other: ProductTotals) -> ProductTotals:
        return ProductTotals(**{f.name: getattr(self, f.name) + getattr(other, f.name)
                                for f in fields(self)})

    @property
    def priced(self) -> int:
        return self.intervals - self.skipped

    @property
    def overstatement_rate(self) -> float:
        """Share of intervals where K > D."""
        return self.overstated / self.intervals if self.intervals else 0.0


@dataclass(frozen=True)
class DayResult:
    day: dt.date
    products: dict[str, ProductTotals]


@dataclass(frozen=True)
class Scorecard:
    """One policy on one scenario, seed and fleet case, over a run of operating days.

    The fleet case is a quantile mock ("P10" ... "P90") or "stochastic", and
    `observed_case` is the case the policy saw: the typical quantile in the
    "typical" view, or the fleet case itself in the "per_case" view. Only
    per-day sums and counts are stored; totals are summed on demand, so a range
    scored day by day (say, on the run farm) combines to exactly the same
    scorecard as a single run over the range.
    """

    policy: str
    scenario: str
    seed: int
    fleet_case: str
    policy_view: str
    observed_case: str
    days: tuple[DayResult, ...]

    @staticmethod
    def combine(cards: Iterable[Scorecard]) -> Scorecard:
        """Merge scorecards of the same policy, scenario, seed, fleet case and view over disjoint days."""
        cards = list(cards)
        labels = {(c.policy, c.scenario, c.seed, c.fleet_case, c.policy_view, c.observed_case) for c in cards}
        if len(labels) != 1:
            raise ValueError("can only combine scorecards of one policy, scenario, seed, fleet case and view; "
                             f"got {sorted(labels)}")
        days = sorted((d for c in cards for d in c.days), key=lambda d: d.day)
        repeated = sorted({a.day for a, b in zip(days, days[1:]) if a.day == b.day})
        if repeated:
            raise ValueError(f"days scored more than once: {', '.join(map(str, repeated))}")
        return Scorecard(*labels.pop(), days=tuple(days))

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

    def to_dict(self) -> dict[str, Any]:
        """JSON form. Totals also carry the rates derived from them."""
        return {
            "policy": self.policy,
            "scenario": self.scenario,
            "seed": self.seed,
            "fleet_case": self.fleet_case,
            "policy_view": self.policy_view,
            "observed_case": self.observed_case,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "totals": {p: {**asdict(t), "overstatement_rate": t.overstatement_rate}
                       for p, t in self.totals.items()},
            "days": [{"day": d.day.isoformat(), "products": {p: asdict(t) for p, t in d.products.items()}}
                     for d in self.days],
        }


def to_json(cards: Sequence[Scorecard]) -> str:
    """The scorecards of one run (one per fleet case) as JSON."""
    return json.dumps({"scorecards": [c.to_dict() for c in cards]}, indent=2) + "\n"


def render(cards: Sequence[Scorecard]) -> str:
    """The scorecards of one run (one per fleet case) as a terminal table."""
    first = cards[0]
    n = len(first.days)
    view = (f"the policy saw the {first.observed_case} fleet, scored against each case"
            if first.policy_view == "typical" else "the policy saw each case's own fleet")
    lines = [
        f"{first.policy} | scenario {first.scenario} | seed {first.seed}",
        f"{first.start} to {first.end} ({n} day{'s' if n != 1 else ''}) | {first.policy_view} view: {view}",
        "",
    ]
    header = ("Fleet case", "Product", "Revenue $", "Given up $", "Deliverable MW-h", "Over-sold MW-h",
              "Under-sold MW-h", "Overstated")
    rows = [(c.fleet_case, p, f"{t.revenue:,.2f}", f"{t.revenue_given_up:,.2f}", f"{t.deliverable_mw_h:,.1f}",
             f"{t.oversold_mw_h:,.2f}", f"{t.undersold_mw_h:,.2f}", f"{t.overstatement_rate:.1%}")
            for c in cards for p, t in c.totals.items()]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        lines.append("  ".join(cell.ljust(w) if i < 2 else cell.rjust(w)
                               for i, (cell, w) in enumerate(zip(row, widths))))
    skipped = {p: t.skipped for p, t in first.totals.items() if t.skipped}  # the same for every case
    if skipped:
        lines.append("\nLeft out of $, settlement price missing: "
                     + ", ".join(f"{p} {s:,} intervals" for p, s in skipped.items()))
    return "\n".join(lines) + "\n"
