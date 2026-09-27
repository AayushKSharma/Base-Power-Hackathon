"""Scorecard: what a policy earned and risked over a run, kept as per-day sums and counts."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields
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
    # Physical shortfall, only in intervals ERCOT deployed: max(award - D, 0) x hours.
    shortfall_mw_h: float = 0.0
    shortfall_cost: float = 0.0  # the scenario's dollar preset, plus compliance
    net: float = 0.0  # revenue - shortfall cost

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
    backup_floor_violations: int = 0
    calm: dict[str, ProductTotals] = field(default_factory=dict)
    scarce: dict[str, ProductTotals] = field(default_factory=dict)
    # Max shortfall MW in each clock hour, in order. The exceedance curve is built from these.
    hourly_shortfall_mw: dict[str, tuple[float, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExceedancePoint:
    """One point on P(hourly shortfall >= x). `fleet_pct` is x as a percent of fleet nameplate."""

    mw: float
    fleet_pct: float
    probability: float


@dataclass(frozen=True)
class ToleranceRow:
    """P(under-serve >= x MW in an hour), beside what the run earned and gave up."""

    mw: float
    probability: float
    revenue: float
    revenue_given_up: float


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

    @property
    def faults(self) -> FaultCounts:
        """Faults from the decision passes that produced this card, summed over days."""
        total = FaultCounts()
        for day in self.days:
            total += day.faults
        return total

    def _regime(self, name: str) -> dict[str, ProductTotals]:
        out: dict[str, ProductTotals] = {}
        for day in self.days:
            for product, totals in getattr(day, name).items():
                out[product] = out.get(product, ProductTotals()) + totals
        return out

    @property
    def calm(self) -> dict[str, ProductTotals]:
        """Totals over intervals the dataset does not flag scarce, per product."""
        return self._regime("calm")

    @property
    def scarce(self) -> dict[str, ProductTotals]:
        """Totals over intervals the dataset flags scarce, per product."""
        return self._regime("scarce")

    def exceedance(self, product: str, grid_mw: Sequence[float], fleet_mw: float) -> tuple[ExceedancePoint, ...]:
        """P(hourly shortfall >= x) for each x, in MW and as a percent of `fleet_mw`."""
        hours = [mw for day in self.days for mw in day.hourly_shortfall_mw.get(product, ())]
        n = len(hours)
        points = []
        for x in grid_mw:
            probability = sum(mw >= x for mw in hours) / n if n else 0.0
            pct = 100.0 * x / fleet_mw if fleet_mw else 0.0
            points.append(ExceedancePoint(float(x), pct, probability))
        return tuple(points)

    def tolerance(self, product: str, grid_mw: Sequence[float]) -> tuple[ToleranceRow, ...]:
        """The exceedance probabilities beside this product's revenue and revenue given up."""
        totals = self.totals[product]
        curve = self.exceedance(product, grid_mw, fleet_mw=0.0)
        return tuple(ToleranceRow(point.mw, point.probability, totals.revenue, totals.revenue_given_up)
                     for point in curve)

    @property
    def backup_floor_violations(self) -> int:
        """Homes pushed through the backup floor. The clamp keeps this at zero."""
        return sum(day.backup_floor_violations for day in self.days)

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
            "faults": asdict(self.faults),
            "backup_floor_violations": self.backup_floor_violations,
            "inputs": input_labels(),
            "calm": {p: {**asdict(t), "overstatement_rate": t.overstatement_rate} for p, t in self.calm.items()},
            "scarce": {p: {**asdict(t), "overstatement_rate": t.overstatement_rate}
                       for p, t in self.scarce.items()},
            "days": [{"day": d.day.isoformat(),
                      "products": {p: asdict(t) for p, t in d.products.items()},
                      "faults": asdict(d.faults),
                      "backup_floor_violations": d.backup_floor_violations,
                      "calm": {p: asdict(t) for p, t in d.calm.items()},
                      "scarce": {p: asdict(t) for p, t in d.scarce.items()},
                      "hourly_shortfall_mw": d.hourly_shortfall_mw}
                     for d in self.days],
        }


def input_labels() -> dict[str, str]:
    """Which scorecard inputs are measured ERCOT data and which are scenario assumptions."""
    return {
        "RT MCPC": "real data",
        "load-zone RT price": "real data",
        "scarcity flag": "assumption",
        "deployment probability": "assumption",
        "fleet, SOC, and failures": "assumption",
        "refill rate": "assumption",
        "shortfall-cost preset": "assumption",
        "compliance cost": "assumption",
        "Set Point Deviation rate": "assumption",
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
        _faults_line(cards),
        _violations_line(cards),
        "",
    ]
    header = ("Fleet case", "Product", "Revenue $", "Given up $", "Net $", "Shortfall $", "Shortfall MW-h",
              "Deliverable MW-h", "Over-sold MW-h", "Under-sold MW-h", "Overstated")
    rows = [(c.fleet_case, p, f"{t.revenue:,.2f}", f"{t.revenue_given_up:,.2f}", f"{t.net:,.2f}",
             f"{t.shortfall_cost:,.2f}", f"{t.shortfall_mw_h:,.3f}", f"{t.deliverable_mw_h:,.1f}",
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


def _violations_line(cards: Sequence[Scorecard]) -> str:
    counts = {card.fleet_case: card.backup_floor_violations for card in cards}
    if len(set(counts.values())) == 1:
        return f"backup-floor violations {next(iter(counts.values()))}"
    return "backup-floor violations " + "  ".join(f"{case} {n}" for case, n in counts.items())


def _faults_line(cards: Sequence[Scorecard]) -> str:
    """Fault counts for the decision passes. Copies shared by every case are printed once."""
    counts = {card.fleet_case: card.faults for card in cards}
    if len(set(counts.values())) == 1:
        faults = next(iter(counts.values()))
        return (f"timeouts {faults.timeouts}  malformed {faults.malformed}"
                f"  restarts {faults.restarts}  fallbacks {faults.fallbacks}")
    return "  ".join(
        f"{case}: timeouts {faults.timeouts} malformed {faults.malformed}"
        f" restarts {faults.restarts} fallbacks {faults.fallbacks}"
        for case, faults in counts.items())
