"""Hour-of-day chart of the calibrated P10–P90 band.

The y scale is linear from share 1 at `data-plot-top` to share 0 at
`data-plot-bottom`. Hour h that has runs is a vertical line at
`data-plot-left + h * (data-plot-right - data-plot-left) / 23`.
Hours with no runs are not drawn.
"""

from __future__ import annotations

import pandas as pd

LEFT, RIGHT, TOP, BOTTOM = 48.0, 624.0, 24.0, 240.0
WIDTH, HEIGHT = 680, 280


def bands_svg(hours: pd.DataFrame) -> str:
    """`hours` has one row per hour that has runs, and columns P10..P90.

    Each hour is its own vertical band. Hours with no runs are not drawn.
    """
    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}"
     data-plot-left="{LEFT:g}" data-plot-right="{RIGHT:g}" data-plot-top="{TOP:g}" data-plot-bottom="{BOTTOM:g}">
  <title>Calibrated quantile bands by hour of day</title>
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{LEFT}" y="16" font-family="sans-serif" font-size="12">Flexible share of fleet by hour of day (CPT)</text>
  <g id="band">{_segments(hours, "P10", "P90", "#c5d8ef", 8)}</g>
  <g id="inner">{_segments(hours, "P25", "P75", "#7aa3d4", 4)}</g>
  <g id="p50">{_segments(hours, "P50", "P50", "#1d3d63", 2)}</g>
  {_x_labels()}
  {_y_labels()}
</svg>
"""


def _segments(hours: pd.DataFrame, low: str, high: str, color: str, width: float) -> str:
    lines = []
    for i in range(len(hours)):
        hour = int(hours["hour"].iloc[i])
        y1, y2 = _y(float(hours[high].iloc[i])), _y(float(hours[low].iloc[i]))
        lines.append(
            f'<line x1="{_x(hour):.6f}" y1="{y1:.6f}" x2="{_x(hour):.6f}" y2="{y2:.6f}" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'
        )
    return "".join(lines)


def _x(hour: int) -> float:
    return LEFT + hour * (RIGHT - LEFT) / 23


def _y(share: float) -> float:
    return TOP + (1 - share) * (BOTTOM - TOP)


def _x_labels() -> str:
    return "\n  ".join(
        f'<text x="{_x(hour):.1f}" y="{BOTTOM + 16}" font-family="sans-serif" font-size="10" '
        f'text-anchor="middle">{hour}</text>'
        for hour in range(0, 24, 6)
    )


def _y_labels() -> str:
    return "\n  ".join(
        f'<text x="{LEFT - 6}" y="{_y(share) + 3:.1f}" font-family="sans-serif" font-size="10" '
        f'text-anchor="end">{share:.1f}</text>'
        for share in (0.0, 0.5, 1.0)
    )
