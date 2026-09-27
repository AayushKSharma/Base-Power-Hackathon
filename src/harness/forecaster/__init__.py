"""Price forecasters and the accuracy grade of their quantile trajectories.

    from harness.forecaster import grade

A forecaster is given a point-in-time observation and returns quantile prices
for the next hours. `grade` scores those trajectories against realized
load-zone RT prices and RT MCPC.
"""

from harness.forecaster.external import ExternalForecaster
from harness.forecaster.grade import GradeReport, MetricCell, grade
from harness.forecaster.reference import (
    dam_as_forecast, net_load, oracle, persistence, rtd_indicative,
)

__all__ = [
    "ExternalForecaster",
    "GradeReport",
    "MetricCell",
    "dam_as_forecast",
    "grade",
    "net_load",
    "oracle",
    "persistence",
    "rtd_indicative",
]
