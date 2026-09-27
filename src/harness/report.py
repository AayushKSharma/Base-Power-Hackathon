"""A short markdown report of one run: reliability first, dollars second."""

from __future__ import annotations

from harness.products import PRODUCTS
from harness.runner import RunResult
from harness.scenario import Scenario
from harness.scorecard import Scorecard, input_labels


def render_report(result: RunResult, scenario: Scenario) -> str:
    """Summarise `result` and chart each fleet case's shortfall exceedance curve."""
    cards = list(result.scorecards.values())
    first = cards[0]
    fleet_mw = scenario.fleet.homes * scenario.fleet.inverter_kw / 1000
    if first.policy_view == "typical":
        saw = f"The policy saw the {first.observed_case} fleet."
    else:
        saw = "The policy saw each fleet case."
    lines = [
        f"# {first.policy} on {first.scenario}",
        "",
        f"{first.start} to {first.end}, seed {first.seed}. {saw}",
        "",
        "## Where the numbers come from",
        "",
        "Shortfall megawatt-hours are physical. The dollar shortfall is a secondary view "
        f"({scenario.scoring.preset}, plus ${scenario.scoring.compliance_per_mw:g}/MW of compliance).",
        "",
    ]
    lines += [f"- {name}: {kind}" for name, kind in input_labels().items()]
    lines += ["", "## Scorecard", ""]
    for card in cards:
        lines += _case_summary(card)
        for product in PRODUCTS:
            lines += ["", f"### {product} exceedance, {card.fleet_case}", ""]
            lines += exceedance_chart(card, product, scenario.scoring.exceedance_mw, fleet_mw)
            lines += ["", f"### {product} tolerance, {card.fleet_case}", ""]
            lines += _tolerance(card, product, scenario.scoring.tolerance_mw)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _case_summary(card: Scorecard) -> list[str]:
    lines = [f"### {card.fleet_case}", "",
             f"Backup-floor violations: {card.backup_floor_violations}.", ""]
    for product, totals in card.totals.items():
        calm, scarce = card.calm[product], card.scarce[product]
        lines.append(
            f"- **{product}:** revenue ${totals.revenue:,.2f}, revenue given up ${totals.revenue_given_up:,.2f}, "
            f"net ${totals.net:,.2f}, shortfall cost ${totals.shortfall_cost:,.2f}, "
            f"shortfall {totals.shortfall_mw_h:,.3f} MW-h, {totals.skipped} intervals left out of $."
        )
        lines.append(
            f"  Calm {calm.intervals} intervals (shortfall {calm.shortfall_mw_h:,.3f} MW-h), "
            f"scarce {scarce.intervals} (shortfall {scarce.shortfall_mw_h:,.3f} MW-h)."
        )
    return lines


def exceedance_chart(card: Scorecard, product: str, grid: tuple[float, ...], fleet_mw: float) -> list[str]:
    """A text bar chart of P(hourly shortfall >= x)."""
    lines = ["```", "MW (share of fleet)      P(hour >= x)"]
    for point in card.exceedance(product, grid, fleet_mw):
        bar = "#" * round(point.probability * 20)
        lines.append(f"{point.mw:g} MW ({point.fleet_pct:.0f}% of fleet)  {point.probability:.1%}  {bar}".rstrip())
    lines.append("```")
    return lines


def _tolerance(card: Scorecard, product: str, grid: tuple[float, ...]) -> list[str]:
    lines = ["| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |",
             "| ---: | ---: | ---: | ---: |"]
    for row in card.tolerance(product, grid):
        lines.append(f"| {row.mw:g} | {row.probability:.1%} | {row.revenue:,.2f} | {row.revenue_given_up:,.2f} |")
    return lines
