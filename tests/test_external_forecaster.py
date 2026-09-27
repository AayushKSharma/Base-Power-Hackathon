"""External forecasters speak the policy protocol's forecast message.

Timeouts, malformed replies and crashes use the same fallback and restart
counts as an external policy.
"""

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from harness.forecaster import grade
from harness.scorecard import FaultCounts

ROOT = Path(__file__).resolve().parents[1]
FIXED = ROOT / "tests" / "forecasters" / "constant_forecaster.py"
DAY = dt.date(2026, 3, 8)
AT_0700 = "2026-03-08T07:00:00+00:00"


def test_an_external_forecaster_matches_the_in_process_forecast():
    frame = _hour("2026-03-08 06:00", 10.0)

    class Fixed:
        name = "fixed"
        look_ahead = False

        def forecast(self, observation, *, horizon_hours, quantiles):
            row = [7.0] * horizon_hours
            return {
                "issued_at": observation["now"]["interval_start_utc"],
                "horizon_hours": horizon_hours,
                "series": {
                    name: {"quantiles": list(quantiles), "values": [row[:] for _ in quantiles]}
                    for name in ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST", "MCPC_ECRS", "MCPC_NSPIN")
                },
            }

    from harness.forecaster import ExternalForecaster

    command = [sys.executable, str(FIXED), "--price", "7"]
    with ExternalForecaster(command) as external:
        report = grade([Fixed(), external], DAY, DAY, market=lambda start, end: frame,
                       horizon_hours=1, quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    internal, through_protocol = report.scores
    assert through_protocol.name == "fixed"
    assert through_protocol.look_ahead is False
    assert through_protocol.faults == FaultCounts()
    assert through_protocol.overall.mae == internal.overall.mae == 3.0


def test_a_slow_forecaster_times_out_and_the_next_hour_reuses_the_last_good_forecast():
    """06:00 realizes at 4 and the reply is 4. 07:00 realizes at 10 and never replies.

    The fallback repeats 4, so the errors are 0 and 6 and the MAE is 3.
    """
    from harness.forecaster import ExternalForecaster

    frame = pd.concat([_hour("2026-03-08 06:00", 4.0), _hour("2026-03-08 07:00", 10.0)])
    command = [sys.executable, str(FIXED), "--price", "4", "--sleep", "5", "--at", AT_0700]

    with ExternalForecaster(command, timeout_s=0.2) as external:
        score = _grade(external, frame)

    assert score.faults == FaultCounts(timeouts=1, fallbacks=1)
    assert score.overall.mae == 3.0


def test_a_forecaster_that_crashes_is_restarted_and_the_grade_completes():
    from harness.forecaster import ExternalForecaster

    frame = pd.concat([_hour("2026-03-08 06:00", 4.0), _hour("2026-03-08 07:00", 10.0)])
    command = [sys.executable, str(FIXED), "--price", "4", "--crash-at", AT_0700]

    with ExternalForecaster(command, timeout_s=2) as external:
        score = _grade(external, frame)

    assert score.faults == FaultCounts(restarts=1, fallbacks=1)
    assert score.overall.mae == 3.0


def test_a_malformed_forecast_keeps_the_process_and_counts_a_fallback():
    from harness.forecaster import ExternalForecaster

    frame = pd.concat([
        _hour("2026-03-08 06:00", 4.0),
        _hour("2026-03-08 07:00", 10.0),
        _hour("2026-03-08 08:00", 4.0),
    ])
    command = [sys.executable, str(FIXED), "--price", "4", "--bad-at", AT_0700]

    with ExternalForecaster(command, timeout_s=2) as external:
        score = _grade(external, frame)

    assert score.faults == FaultCounts(malformed=1, fallbacks=1)
    # Hours realize at 4, 10, 4. The bad hour repeats 4, so the errors are 0, 6, 0.
    assert score.overall.mae == 2.0


def _grade(forecaster, frame):
    report = grade([forecaster], DAY, DAY, market=lambda start, end: frame,
                   horizon_hours=1, quantiles=(0.1, 0.5, 0.9), spike_top=0.1)
    return report.scores[0]


def _hour(start, price):
    index = pd.date_range(start, periods=12, freq="5min", tz="UTC", name="interval_start_utc")
    columns = {
        "interval_start_cpt": index.tz_convert("America/Chicago"),
        "operating_day": pd.Timestamp("2026-03-08"),
        "scarce_ecrs": False,
        "scarce_nspin": False,
    }
    for suffix in ("houston", "north", "south", "west"):
        columns[f"lz_spp_{suffix}"] = price
    columns["rt_mcpc_5m_ecrs"] = price
    columns["rt_mcpc_5m_nspin"] = price
    return pd.DataFrame(columns, index=index)
