"""Matplotlib charts for one `harness compare` run. The Agg backend needs no display."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from harness.products import PRODUCTS
from harness.runner import RunResult
from harness.scenario import Scenario
from harness.scorecard import Scorecard

Groups = list[tuple[Scenario, list[RunResult]]]


def write_charts(groups: Groups, intervals: pd.DataFrame, out: Path, day: dt.date,
                 curve_names: tuple[str, ...]) -> None:
    """Frontier, exceedance, one day's capability, and net $ by policy and scenario."""
    out.mkdir(parents=True, exist_ok=True)
    _save(_frontier(groups, curve_names), out / "frontier.png")
    _save(_exceedance(groups), out / "exceedance.png")
    _save(_capability(intervals, day), out / "capability.png")
    _save(_rankings(groups), out / "rankings.png")
    plt.close("all")


def _save(figure: plt.Figure, path: Path) -> None:
    figure.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(figure)


def _frontier(groups: Groups, curve_names: tuple[str, ...]) -> plt.Figure:
    figure, axes = plt.subplots(1, len(groups), figsize=(5.4 * len(groups), 4.4), squeeze=False)
    order = {name: index for index, name in enumerate(curve_names)}
    last = axes[0][-1]
    for ax, (scenario, results) in zip(axes[0], groups):
        curve = []
        for result in results:
            name, revenue, shortfall = _point(result)
            if name in curve_names:
                curve.append((name, revenue, shortfall))
                continue
            ax.scatter([revenue], [shortfall], zorder=3, label=name if ax is last else None)
        if curve:
            curve.sort(key=lambda item: order[item[0]])
            ax.plot([revenue for _, revenue, _ in curve], [shortfall for _, _, shortfall in curve],
                    marker="o", label="reliability target" if ax is last else None)
        ax.invert_yaxis()
        ax.set_title(scenario.name)
        ax.set_xlabel("Revenue $")
        ax.set_ylabel("Shortfall MW-h")
    last.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.02, 0.5))
    figure.suptitle("Revenue versus reliability")
    return figure


def _exceedance(groups: Groups) -> plt.Figure:
    figure, axes = plt.subplots(len(groups), len(PRODUCTS), figsize=(5 * len(PRODUCTS), 3.4 * len(groups)),
                                squeeze=False, layout="constrained")
    for row, (scenario, results) in enumerate(groups):
        fleet_mw = scenario.fleet.homes * scenario.fleet.inverter_kw / 1000
        for col, product in enumerate(PRODUCTS):
            ax = axes[row][col]
            for result in results:
                card = _observed(result)
                points = card.exceedance(product, scenario.scoring.exceedance_mw, fleet_mw)
                ax.plot([point.mw for point in points], [point.probability for point in points],
                        marker="o", label=card.policy)
            ax.set_ylim(-0.05, 1.05)
            ax.set_title(f"{scenario.name} {product}")
            ax.set_xlabel("MW")
            ax.set_ylabel("P(hour ≥ x)")
            ax.legend(fontsize=6)
    figure.suptitle("Shortfall exceedance")
    return figure


def _capability(intervals: pd.DataFrame, day: dt.date) -> plt.Figure:
    """Reported capability, true deliverable MW, and award on `day`, deployments marked.

    Solid is reported MW, dashed is true deliverable, dotted is the award.
    A triangle marks an interval ERCOT deployed.
    """
    frame = _on_day(intervals, day)
    scenarios = list(dict.fromkeys(frame["scenario"])) or ["none"]
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    figure, axes = plt.subplots(len(scenarios), len(PRODUCTS),
                                figsize=(6.4 * len(PRODUCTS), 3.8 * len(scenarios)), squeeze=False,
                                layout="constrained")
    for row, scenario in enumerate(scenarios):
        for col, product in enumerate(PRODUCTS):
            ax = axes[row][col]
            part = frame[(frame["scenario"] == scenario) & (frame["product"] == product)]
            if not part.empty:
                part = part[part["fleet_case"] == part["observed_case"]]
            marked = False
            for index, (policy, rows) in enumerate(part.groupby("policy", sort=False)):
                color = colors[index % len(colors)]
                rows = rows.sort_values("interval_start_cpt")
                when = pd.to_datetime(rows["interval_start_cpt"])
                ax.plot(when, rows["reported_mw"], color=color, label=str(policy))
                ax.plot(when, rows["deliverable_mw"], color=color, linestyle="--")
                ax.plot(when, rows["award_mw"], color=color, linestyle=":")
                hits = rows[rows["deployed"].astype(bool)]
                if not hits.empty:
                    ax.scatter(pd.to_datetime(hits["interval_start_cpt"]), hits["award_mw"],
                               color=color, marker="v", s=28, zorder=4,
                               label="deployment" if not marked else None)
                    marked = True
            ax.plot([], [], color="black", linestyle="--", label="deliverable")
            ax.plot([], [], color="black", linestyle=":", label="award")
            ax.set_title(f"{scenario} {product}")
            ax.set_ylabel("MW")
            ax.legend(fontsize=6)
            ax.tick_params(axis="x", labelrotation=30)
    figure.suptitle(f"Reported capability, deliverable MW, and award on {day}")
    return figure


def _rankings(groups: Groups) -> plt.Figure:
    figure, ax = plt.subplots(figsize=(8.5, 4.4))
    policies = list(dict.fromkeys(_observed(result).policy for _, results in groups for result in results))
    positions = range(len(policies))
    width = 0.8 / max(len(groups), 1)
    for index, (scenario, results) in enumerate(groups):
        by_policy = {_observed(result).policy: _net(result) for result in results}
        offset = (index - (len(groups) - 1) / 2) * width
        ax.bar([pos + offset for pos in positions], [by_policy.get(policy, 0.0) for policy in policies],
               width=width, label=scenario.name)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(policies, rotation=20, ha="right")
    ax.set_ylabel("Net $")
    ax.set_title(_ranking_title(groups))
    ax.legend(fontsize=8)
    return figure


def _ranking_title(groups: Groups) -> str:
    orders = {
        scenario.name: tuple(policy for policy, _ in sorted(
            ((_observed(result).policy, _net(result)) for result in results),
            key=lambda item: (-item[1], item[0])))
        for scenario, results in groups
    }
    names = list(orders)
    if len(names) < 2 or all(orders[names[0]] == orders[other] for other in names[1:]):
        return "Net $ by policy and scenario (rankings hold)"
    return "Net $ by policy and scenario (rankings flip)"


def _on_day(intervals: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    if intervals.empty:
        return intervals
    dates = pd.to_datetime(intervals["operating_day"]).dt.date
    return intervals[dates == day]


def _observed(result: RunResult) -> Scorecard:
    card = next(iter(result.scorecards.values()))
    return result.scorecards.get(card.observed_case, card)


def _point(result: RunResult) -> tuple[str, float, float]:
    card = _observed(result)
    totals = card.totals.values()
    return (card.policy,
            sum(item.revenue for item in totals),
            sum(item.shortfall_mw_h for item in totals))


def _net(result: RunResult) -> float:
    return sum(item.net for item in _observed(result).totals.values())
