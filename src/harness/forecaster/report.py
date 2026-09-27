"""Leaderboard text and charts for a forecast grade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.forecaster.grade import HORIZONS, REGIMES, GradeReport, MetricCell
from harness.scorecard import FaultCounts

CHARTS = ("mae.svg", "pinball.svg", "horizon.svg", "regime.svg")


def render(report: GradeReport) -> str:
    """A text leaderboard. Look-ahead forecasters are marked; they saw realized prices."""
    lines = [
        "look-ahead forecasters saw realized prices and are not a fair entry.",
        f"{'forecaster':<24} {'ahead':>5} {'n':>6} {'MAE':>8} {'RMSE':>8} "
        f"{'pinball50':>9} {'cover':>7} {'spikeP':>7} {'spikeR':>7} {'fb':>4}",
    ]
    ranked = sorted(report.scores, key=lambda score: (score.overall.mae is None, score.overall.mae or 0.0))
    for score in ranked:
        cell = score.overall
        lines.append(
            f"{score.name:<24} {'YES' if score.look_ahead else 'no':>5} {cell.n:6d} "
            f"{_num(cell.mae)} {_num(cell.rmse)} {_num(cell.pinball.get(0.5))} "
            f"{_num(cell.coverage)} {_num(cell.spike_precision)} {_num(cell.spike_recall)} "
            f"{score.faults.fallbacks:4d}"
        )
    lines.append("")
    lines.append("by horizon (MAE)")
    lines.append(f"{'forecaster':<24} {'next_hour':>10} {'1-6h':>10} {'6-24h':>10}")
    for score in ranked:
        lines.append(
            f"{score.name:<24} {_num(score.by_horizon['next_hour'].mae)} "
            f"{_num(score.by_horizon['h1_6'].mae)} {_num(score.by_horizon['h6_24'].mae)}"
        )
    lines.append("")
    lines.append("by regime (MAE)")
    lines.append(f"{'forecaster':<24} {'calm':>10} {'scarce':>10}")
    for score in ranked:
        lines.append(
            f"{score.name:<24} {_num(score.by_regime['calm'].mae)} {_num(score.by_regime['scarce'].mae)}"
        )
    lines.append("horizon buckets: next_hour (lead < 1h), 1-6h (lead from 1h up to 6h), "
                 "6-24h (lead from 6h through 24h).")
    return "\n".join(lines) + "\n"


def publish(report: GradeReport, out: Path) -> str:
    """Write leaderboard.json and the charts. Returns the text leaderboard."""
    out.mkdir(parents=True, exist_ok=True)
    text = render(report)
    (out / "leaderboard.txt").write_text(text)
    (out / "leaderboard.json").write_text(json.dumps(_payload(report), indent=2) + "\n")
    (out / "mae.svg").write_text(_bars(
        "MAE on the median",
        [(score.name, score.look_ahead, score.overall.mae) for score in report.scores],
    ))
    (out / "pinball.svg").write_text(_bars(
        "Pinball loss at the median",
        [(score.name, score.look_ahead, score.overall.pinball.get(0.5)) for score in report.scores],
    ))
    (out / "horizon.svg").write_text(_grouped(report, HORIZONS, "MAE by horizon bucket"))
    (out / "regime.svg").write_text(_grouped(report, REGIMES, "MAE in calm and scarce hours"))
    return text


def _payload(report: GradeReport) -> dict[str, Any]:
    return {
        "start": None if report.start is None else report.start.isoformat(),
        "end": None if report.end is None else report.end.isoformat(),
        "quantiles": list(report.quantiles),
        "spike_top": report.spike_top,
        "forecasters": [_forecaster(score) for score in report.scores],
    }


def _forecaster(score: Any) -> dict[str, Any]:
    return {
        "name": score.name,
        "look_ahead": score.look_ahead,
        "faults": _faults(score.faults),
        **_cell(score.overall),
        "by_horizon": {name: _cell(cell) for name, cell in score.by_horizon.items()},
        "by_regime": {name: _cell(cell) for name, cell in score.by_regime.items()},
        "by_horizon_regime": {
            horizon: {regime: _cell(cell) for regime, cell in regimes.items()}
            for horizon, regimes in score.by_horizon_regime.items()
        },
        "by_series": {name: _cell(cell) for name, cell in score.by_series.items()},
    }


def _cell(cell: MetricCell) -> dict[str, Any]:
    return {
        "n": cell.n,
        "mae": cell.mae,
        "rmse": cell.rmse,
        "pinball": {str(q): loss for q, loss in cell.pinball.items()},
        "coverage": cell.coverage,
        "spike_precision": cell.spike_precision,
        "spike_recall": cell.spike_recall,
    }


def _faults(faults: FaultCounts) -> dict[str, int]:
    return {
        "timeouts": faults.timeouts,
        "malformed": faults.malformed,
        "restarts": faults.restarts,
        "fallbacks": faults.fallbacks,
    }


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(value: float | None) -> str:
    return f"{value:8.3f}" if value is not None else f"{'n/a':>8}"


def _bars(title: str, rows: list[tuple[str, bool, float | None]]) -> str:
    width, bar_w, row_h = 720, 360, 28
    height = 48 + row_h * max(1, len(rows))
    peak = max((value or 0.0) for _name, _ahead, value in rows) if rows else 0.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<text x="16" y="24" font-family="sans-serif" font-size="16">{_xml(title)}</text>',
    ]
    for i, (name, ahead, value) in enumerate(rows):
        label = f"{name} (look-ahead)" if ahead else name
        span = 0 if peak == 0 or value is None else bar_w * (value / peak)
        y = 40 + i * row_h
        parts.append(f'<text x="16" y="{y + 16}" font-family="sans-serif" font-size="13">{_xml(label)}</text>')
        parts.append(f'<rect x="280" y="{y}" width="{span:.1f}" height="18" fill="#3d5a40"/>')
        shown = "n/a" if value is None else f"{value:.3f}"
        parts.append(
            f'<text x="{288 + span:.1f}" y="{y + 14}" font-family="sans-serif" font-size="12">{shown}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _grouped(report: GradeReport, keys: tuple[str, ...], title: str) -> str:
    rows = []
    cells = report.scores
    for score in cells:
        source = score.by_horizon if keys == HORIZONS else score.by_regime
        for key in keys:
            rows.append((f"{score.name} {key}", score.look_ahead, source[key].mae))
    return _bars(title, rows)
