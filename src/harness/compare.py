"""The part of `harness compare` this ticket needs: one run per named policy,
a revenue-vs-shortfall frontier, and each policy's existing exceedance chart.

The fuller comparison suite (identical-draw checks, the reliability-target
curve, ranking flips, the insight note) belongs to a later ticket.
"""

from __future__ import annotations

from pathlib import Path

from harness.products import PRODUCTS
from harness.report import exceedance_chart
from harness.runner import RunResult
from harness.scenario import Scenario

LEFT, RIGHT, TOP, BOTTOM = 64.0, 560.0, 32.0, 280.0
WIDTH, HEIGHT = 640, 320


def write_comparison(results: list[RunResult], scenario: Scenario, out: Path,
                     forecast_value: str | None = None) -> None:
    """Write frontier.svg and exceedance.md for the scored policies.

    `forecast_value`, when a forecaster was used, is appended as a report section.
    """
    out.mkdir(parents=True, exist_ok=True)
    (out / "frontier.svg").write_text(frontier_svg([_point(result) for result in results]))
    text = _exceedance(results, scenario)
    if forecast_value:
        text = text.rstrip() + "\n\n" + forecast_value.strip() + "\n"
    (out / "exceedance.md").write_text(text)


def frontier_svg(points: list[tuple[str, float, float]]) -> str:
    """One point per policy: revenue on x, shortfall MW-h on y (up is more reliable)."""
    revenues = [revenue for _, revenue, _ in points]
    shortfalls = [shortfall for _, _, shortfall in points]
    body = []
    for name, revenue, shortfall in points:
        x, y = _x(revenue, revenues), _y(shortfall, shortfalls)
        label = _xml(name)
        body.append(
            f'<circle data-policy="{label}" data-revenue="{revenue}" data-shortfall="{shortfall}" '
            f'cx="{x:.2f}" cy="{y:.2f}" r="5" fill="#1d3d63"/>'
        )
        body.append(
            f'<text x="{x + 8:.2f}" y="{y + 4:.2f}" font-family="sans-serif" font-size="12">{label}</text>'
        )
    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <title>Revenue versus shortfall</title>
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{LEFT}" y="20" font-family="sans-serif" font-size="12">Revenue vs shortfall MW-h</text>
  {"".join(body)}
</svg>
"""


def _point(result: RunResult) -> tuple[str, float, float]:
    """Revenue and shortfall on the fleet case the policy actually saw."""
    card = next(iter(result.scorecards.values()))
    card = result.scorecards.get(card.observed_case, card)
    revenue = sum(totals.revenue for totals in card.totals.values())
    shortfall = sum(totals.shortfall_mw_h for totals in card.totals.values())
    return card.policy, revenue, shortfall


def _exceedance(results: list[RunResult], scenario: Scenario) -> str:
    fleet_mw = scenario.fleet.homes * scenario.fleet.inverter_kw / 1000
    lines = ["# Shortfall exceedance", ""]
    for result in results:
        for card in result.scorecards.values():
            for product in PRODUCTS:
                lines += [f"## {card.policy} {product}, {card.fleet_case}", ""]
                lines += exceedance_chart(card, product, scenario.scoring.exceedance_mw, fleet_mw)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _span(value: float, values: list[float], start: float, end: float) -> float:
    lo, hi = min(values), max(values)
    if hi == lo:
        return (start + end) / 2
    return start + (value - lo) / (hi - lo) * (end - start)


def _x(revenue: float, revenues: list[float]) -> float:
    return _span(revenue, revenues, LEFT, RIGHT)


def _y(shortfall: float, shortfalls: list[float]) -> float:
    # A smaller shortfall sits higher: it is the more reliable outcome.
    return BOTTOM + TOP - _span(shortfall, shortfalls, TOP, BOTTOM)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
