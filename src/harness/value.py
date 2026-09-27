"""The value of a price forecast: one algorithm with a forecaster, the oracle, and persistence."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from harness.forecaster.cli import build_forecaster
from harness.policy import Policy
from harness.products import PRODUCTS
from harness.runner import MarketLoader, RunResult, run
from harness.scenario import Scenario
from harness.scorecard import Scorecard


@dataclass(frozen=True)
class CurvePoint:
    mw: float
    probability: float


@dataclass(frozen=True)
class ArmScore:
    """Revenue, exposure, and the exceedance curve under one forecaster."""

    forecaster: str
    revenue: float
    oversold_mw_h: float
    undersold_mw_h: float
    exceedance: dict[str, tuple[CurvePoint, ...]]


@dataclass(frozen=True)
class ForecastValue:
    """One policy scored with the chosen forecaster, the oracle, and persistence."""

    policy: str
    scenario: str
    forecaster: str
    chosen: ArmScore
    oracle: ArmScore
    persistence: ArmScore


def measure_forecast_value(
    policy_factory: Callable[[], Policy],
    scenario: Scenario,
    start: dt.date,
    end: dt.date,
    seed: int | None,
    market: MarketLoader,
    forecaster: str,
    frame: pd.DataFrame,
    *,
    timeout_s: float = 1.0,
    fallback: str = "last_good",
) -> tuple[ForecastValue, RunResult]:
    """Score `forecaster` against the oracle and persistence. Return that report and the chosen run.

    The oracle has perfect foresight of the prices in `frame`. Persistence repeats
    the latest realized price. The returned run is the one that used `forecaster`.
    """
    chosen_run, chosen_name = _score(
        policy_factory, scenario, start, end, seed, market, frame, forecaster, timeout_s, fallback)
    oracle_run, oracle_name = _score(
        policy_factory, scenario, start, end, seed, market, frame, "oracle", timeout_s, fallback)
    persistence_run, persistence_name = _score(
        policy_factory, scenario, start, end, seed, market, frame, "persistence", timeout_s, fallback)
    chosen = _arm(chosen_run, scenario, chosen_name)
    report = ForecastValue(
        policy=_seen(chosen_run).policy,
        scenario=scenario.name,
        forecaster=chosen_name,
        chosen=chosen,
        oracle=_arm(oracle_run, scenario, oracle_name),
        persistence=_arm(persistence_run, scenario, persistence_name),
    )
    return report, chosen_run


def render_forecast_value(report: ForecastValue) -> str:
    """Markdown section: the three arms, and scorecard deltas against the two baselines."""
    lines = [
        "## Value of the forecast",
        "",
        f"`{report.policy}` with forecaster `{report.forecaster}`.",
        "",
        "| | Revenue $ | Over-sold MW-h | Under-sold MW-h |",
        "| --- | ---: | ---: | ---: |",
        _row(report.chosen),
        _row(report.oracle),
        _row(report.persistence),
        "",
        _versus("oracle", report.chosen, report.oracle),
        _versus("persistence", report.chosen, report.persistence),
        "",
        "Exceedance, versus oracle:",
        *_exceedance_lines(report.chosen, report.oracle),
        "",
        "Exceedance, versus persistence:",
        *_exceedance_lines(report.chosen, report.persistence),
    ]
    return "\n".join(lines) + "\n"


def write_forecast_value(report: ForecastValue, out: Path) -> None:
    """Save value.json and the chart that places the forecaster between persistence and oracle."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "value.json").write_text(json.dumps(forecast_value_dict(report), indent=2) + "\n")
    (out / "value.svg").write_text(value_svg(report))


def forecast_value_dict(report: ForecastValue) -> dict[str, Any]:
    """Scorecard numbers and the deltas of the chosen forecaster against each baseline."""
    return {
        "policy": report.policy,
        "scenario": report.scenario,
        "forecaster": report.forecaster,
        "chosen": _arm_dict(report.chosen),
        "oracle": _arm_dict(report.oracle),
        "persistence": _arm_dict(report.persistence),
        "vs_oracle": _delta_dict(report.chosen, report.oracle),
        "vs_persistence": _delta_dict(report.chosen, report.persistence),
    }


LEFT, RIGHT = 80.0, 560.0


def value_svg(report: ForecastValue) -> str:
    """Revenue line with persistence and the oracle as the ends, and the chosen forecaster on it."""
    anchors = [report.persistence.revenue, report.oracle.revenue]
    points = (
        ("persistence", report.persistence),
        ("oracle", report.oracle),
        ("chosen", report.chosen),
    )
    x_persist = _span(report.persistence.revenue, anchors, LEFT, RIGHT)
    x_oracle = _span(report.oracle.revenue, anchors, LEFT, RIGHT)
    marks = []
    for arm, score in points:
        x = _span(score.revenue, anchors, LEFT, RIGHT)
        label = _xml(score.forecaster)
        marks.append(
            f'<circle data-arm="{arm}" data-forecaster="{label}" data-revenue="{score.revenue}" '
            f'cx="{x:.2f}" cy="160" r="6" fill="#1d3d63"/>'
        )
        marks.append(
            f'<text x="{x:.2f}" y="140" text-anchor="middle" font-family="sans-serif" font-size="12">'
            f'{label}</text>'
        )
    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="200" viewBox="0 0 640 200">
  <title>Value of the forecast</title>
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="80" y="28" font-family="sans-serif" font-size="14">Value of the forecast</text>
  <line x1="{min(x_persist, x_oracle):.2f}" y1="160" x2="{max(x_persist, x_oracle):.2f}" y2="160" stroke="#1d3d63"/>
  {"".join(marks)}
</svg>
"""


def _arm_dict(arm: ArmScore) -> dict[str, Any]:
    return {
        "forecaster": arm.forecaster,
        "revenue": arm.revenue,
        "oversold_mw_h": arm.oversold_mw_h,
        "undersold_mw_h": arm.undersold_mw_h,
        "exceedance": {
            product: [{"mw": point.mw, "probability": point.probability} for point in points]
            for product, points in arm.exceedance.items()
        },
    }


def _delta_dict(chosen: ArmScore, baseline: ArmScore) -> dict[str, Any]:
    return {
        "revenue": chosen.revenue - baseline.revenue,
        "oversold_mw_h": chosen.oversold_mw_h - baseline.oversold_mw_h,
        "undersold_mw_h": chosen.undersold_mw_h - baseline.undersold_mw_h,
        "exceedance": {
            product: [
                {"mw": left.mw, "delta": left.probability - right.probability}
                for left, right in zip(chosen.exceedance[product], baseline.exceedance[product])
            ]
            for product in PRODUCTS
        },
    }


def _span(value: float, values: list[float], start: float, end: float) -> float:
    lo, hi = min(values), max(values)
    if hi == lo:
        return (start + end) / 2
    return start + (value - lo) / (hi - lo) * (end - start)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _score(policy_factory: Callable[[], Policy], scenario: Scenario, start: dt.date, end: dt.date,
           seed: int | None, market: MarketLoader, frame: pd.DataFrame, spec: str,
           timeout_s: float, fallback: str) -> tuple[RunResult, str]:
    forecaster = build_forecaster(spec, frame, timeout_s, fallback)
    name = str(getattr(forecaster, "name", spec))
    result = run(policy_factory(), scenario, start, end, seed, market=market, forecaster=forecaster)
    return result, name


def _arm(result: RunResult, scenario: Scenario, forecaster: str) -> ArmScore:
    card = _seen(result)
    totals = list(card.totals.values())
    fleet_mw = scenario.fleet.homes * scenario.fleet.inverter_kw / 1000
    return ArmScore(
        forecaster=forecaster,
        revenue=sum(item.revenue for item in totals),
        oversold_mw_h=sum(item.oversold_mw_h for item in totals),
        undersold_mw_h=sum(item.undersold_mw_h for item in totals),
        exceedance={
            product: tuple(CurvePoint(point.mw, point.probability)
                           for point in card.exceedance(product, scenario.scoring.exceedance_mw, fleet_mw))
            for product in PRODUCTS
        },
    )


def _seen(result: RunResult) -> Scorecard:
    card = next(iter(result.scorecards.values()))
    return result.scorecards.get(card.observed_case, card)


def _row(arm: ArmScore) -> str:
    label = arm.forecaster or "forecaster"
    return f"| {label} | {arm.revenue:.2f} | {arm.oversold_mw_h:.3f} | {arm.undersold_mw_h:.3f} |"


def _versus(name: str, chosen: ArmScore, baseline: ArmScore) -> str:
    return (
        f"Versus {name}: revenue {chosen.revenue - baseline.revenue:+.2f}, "
        f"over-sold {chosen.oversold_mw_h - baseline.oversold_mw_h:+.3f} MW-h, "
        f"under-sold {chosen.undersold_mw_h - baseline.undersold_mw_h:+.3f} MW-h."
    )


def _exceedance_lines(chosen: ArmScore, baseline: ArmScore) -> list[str]:
    lines = []
    for product in PRODUCTS:
        bits = []
        for left, right in zip(chosen.exceedance[product], baseline.exceedance[product]):
            bits.append(f"{left.mw:g} MW {left.probability - right.probability:+.1%}")
        lines.append(f"- {product}: {', '.join(bits)}")
    return lines
