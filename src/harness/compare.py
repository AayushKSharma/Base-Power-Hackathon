"""`harness compare`: several policies on several scenarios, reliability first.

One frontier and one exceedance chart per the policies that were scored, plus
a side-by-side scorecard, a tolerance table per policy and quantile mock, and
the per-interval data dump.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from harness.policy import Policy, builtin_policy
from harness.compare_charts import write_charts
from harness.products import PRODUCTS
from harness.report import _tolerance, exceedance_chart
from harness.runner import INTERVALS_FILE, RunResult
from harness.scenario import Scenario
from harness.scorecard import Scorecard

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


def write_suite(groups: list[tuple[Scenario, list[RunResult]]], out: Path, day: dt.date) -> None:
    """Scorecard files and charts for every policy and scenario, in scenario order.

    `frontier.svg` is the first scenario. `exceedance.md` covers every scenario.
    The charts are revenue-versus-reliability (with the reliability-target curve),
    exceedance, capability on `day`, and net $ by policy and scenario.
    """
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    exceedance = []
    for scenario, results in groups:
        for result in results:
            card = _observed(result)
            frames.append(result.intervals.assign(policy=card.policy, scenario=scenario.name))
        exceedance.append(_exceedance(results, scenario))
    intervals = pd.concat(frames, ignore_index=True)
    intervals.to_parquet(out / INTERVALS_FILE, index=False)
    (out / "comparison.md").write_text(comparison_report(groups))
    (out / "exceedance.md").write_text("\n".join(exceedance))
    _, first_results = groups[0]
    points = [_point(result) for result in first_results]
    (out / "frontier.svg").write_text(frontier_svg(points, _curve_points(points)))
    write_charts(groups, intervals, out / "charts", day,
                 tuple(curve_name(epsilon) for epsilon in RELIABILITY_EPSILONS))


def comparison_table(groups: list[tuple[Scenario, list[RunResult]]]) -> str:
    """One row per policy and scenario: reliability before dollars, then failure counts."""
    header = ("Scenario", "Policy", "Shortfall MW-h", "Overstated", "Net $", "Regret $",
              "Timeouts", "Malformed", "Restarts", "Fallbacks")
    rows = [_summary_row(scenario, result) for scenario, results in groups for result in results]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = []
    for row in [header, *rows]:
        lines.append("  ".join(cell.ljust(width) if i < 2 else cell.rjust(width)
                               for i, (cell, width) in enumerate(zip(row, widths))))
    return "\n".join(lines) + "\n"


def comparison_report(groups: list[tuple[Scenario, list[RunResult]]]) -> str:
    """Tolerance tables first, then the side-by-side dollars."""
    lines = [
        "# Comparison",
        "",
        "Reliability first: P(under-serve ≥ X MW) beside the revenue given up for that run.",
        "",
        "## Tolerance",
        "",
    ]
    for scenario, results in groups:
        for result in results:
            for card in result.scorecards.values():
                for product in PRODUCTS:
                    lines += [f"### {card.policy} on {scenario.name}, {card.fleet_case}, {product}", ""]
                    lines += _tolerance(card, product, scenario.scoring.tolerance_mw)
                    lines.append("")
    lines += ["## Where shortfalls concentrate", "",
              "Calm is an interval the dataset does not flag scarce. "
              "Shortfall is MW-h on the fleet case the policy saw, summed over products.",
              "",
              "| Scenario | Policy | Calm MW-h | Scarce MW-h |",
              "| --- | --- | ---: | ---: |"]
    for scenario, results in groups:
        for result in results:
            card = _observed(result)
            calm = sum(totals.shortfall_mw_h for totals in card.calm.values())
            scarce = sum(totals.shortfall_mw_h for totals in card.scarce.values())
            lines.append(f"| {scenario.name} | {card.policy} | {calm:,.3f} | {scarce:,.3f} |")
    lines += ["", *_ranking_lines(groups), "## Scorecard", "", comparison_table(groups).rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def _ranking_lines(groups: list[tuple[Scenario, list[RunResult]]]) -> list[str]:
    """Whether net-$ order changes from the first scenario to each later one."""
    orders = {
        scenario.name: [policy for policy, _ in sorted(
            ((_observed(result).policy, _net(result)) for result in results),
            key=lambda item: (-item[1], item[0]))]
        for scenario, results in groups
    }
    lines = ["## Rankings", ""]
    for name, order in orders.items():
        lines.append(f"- {name}, best net $ first: {', '.join(order)}")
    names = list(orders)
    if len(names) >= 2:
        base = names[0]
        for other in names[1:]:
            verb = "hold" if orders[base] == orders[other] else "flip"
            lines.append(f"Rankings {verb} between {base} and {other}.")
    lines.append("")
    return lines


def _net(result: RunResult) -> float:
    return sum(totals.net for totals in _observed(result).totals.values())


def _summary_row(scenario: Scenario, result: RunResult) -> tuple[str, ...]:
    """The fleet case the policy saw, summed over products."""
    card = _observed(result)
    totals = list(card.totals.values())
    intervals = sum(totals_for.intervals for totals_for in totals)
    overstated = sum(totals_for.overstated for totals_for in totals) / intervals if intervals else 0.0
    faults = card.faults
    return (
        scenario.name,
        card.policy,
        f"{sum(totals_for.shortfall_mw_h for totals_for in totals):,.3f}",
        f"{overstated:.1%}",
        f"{sum(totals_for.net for totals_for in totals):,.2f}",
        f"{sum(totals_for.revenue_given_up for totals_for in totals):,.2f}",
        str(faults.timeouts),
        str(faults.malformed),
        str(faults.restarts),
        str(faults.fallbacks),
    )


def _observed(result: RunResult) -> Scorecard:
    """The scorecard for the fleet case the policy actually saw."""
    card = next(iter(result.scorecards.values()))
    return result.scorecards.get(card.observed_case, card)


# The reliability-target policy, swept so the frontier can draw it as a curve.
RELIABILITY_EPSILONS = (0.01, 0.05, 0.1, 0.2)


def curve_name(epsilon: float) -> str:
    return f"reliability_target(epsilon={epsilon:g})"


def curve_policies(scenario: Scenario, already: set[str]) -> list[Policy]:
    """Reliability-target policies for the frontier curve, skipping names already scored."""
    policies: list[Policy] = []
    for epsilon in RELIABILITY_EPSILONS:
        policy = builtin_policy("reliability_target", {"epsilon": f"{epsilon:g}"}, scenario)
        if policy.name not in already:
            policies.append(policy)
    return policies


def frontier_svg(points: list[tuple[str, float, float]],
                 curve: list[tuple[str, float, float]] | None = None) -> str:
    """Named policies as points. `curve` is the reliability target joined in sweep order.

    Revenue is on x. Shortfall MW-h is on y, and up is more reliable.
    """
    curve = list(curve or [])
    seen: set[str] = set()
    ordered: list[tuple[str, float, float]] = []
    for point in [*points, *curve]:
        if point[0] in seen:
            continue
        seen.add(point[0])
        ordered.append(point)
    revenues = [revenue for _, revenue, _ in ordered]
    shortfalls = [shortfall for _, _, shortfall in ordered]
    body = []
    if len(curve) >= 2:
        coords = " ".join(
            f"{_x(revenue, revenues):.2f},{_y(shortfall, shortfalls):.2f}"
            for _, revenue, shortfall in curve
        )
        body.append(
            f'<polyline data-curve="reliability_target" points="{coords}" fill="none" '
            f'stroke="#c45c26" stroke-width="2"/>'
        )
    for name, revenue, shortfall in ordered:
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


def _curve_points(points: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Reliability-target points in epsilon order, for the frontier curve."""
    by_name = {name: point for point in points for name in (point[0],)}
    return [by_name[curve_name(epsilon)] for epsilon in RELIABILITY_EPSILONS if curve_name(epsilon) in by_name]


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
