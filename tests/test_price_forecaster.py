"""Price-forecaster protocol and grading.

The seam is `parse_forecast` / `forecast_message` and `grade`: a forecaster and
a date range produce an accuracy report whose metrics are hand-computed
literals.
"""

import datetime as dt
import json

import pandas as pd
import pytest

from harness.products import LOAD_ZONES

# Quantile trajectories are indexed as values[quantile][hour].
SERIES = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST", "MCPC_ECRS", "MCPC_NSPIN")


def _reply(horizon_hours, rows):
    return {
        "issued_at": "2026-03-08T06:00:00+00:00",
        "horizon_hours": horizon_hours,
        "series": {
            name: {"quantiles": [0.1, 0.5, 0.9], "values": rows}
            for name in SERIES
        },
    }


def test_a_forecast_reply_is_a_quantile_trajectory_for_every_price_series():
    from harness.protocol import forecast_message, parse_forecast

    message = forecast_message(
        {"now": {"interval_start_utc": "2026-03-08T06:00:00+00:00"}},
        horizon_hours=2, quantiles=(0.1, 0.5, 0.9),
    )
    parsed = parse_forecast(
        _reply(2, [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
        issued_at="2026-03-08T06:00:00+00:00",
        horizon_hours=2, quantiles=(0.1, 0.5, 0.9),
    )
    missing = parse_forecast(
        {**_reply(2, [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
         "series": {name: {"quantiles": [0.1, 0.5, 0.9], "values": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]}
                    for name in SERIES if name != "LZ_WEST"}},
        issued_at="2026-03-08T06:00:00+00:00",
        horizon_hours=2, quantiles=(0.1, 0.5, 0.9),
    )

    assert message == {
        "type": "forecast",
        "horizon_hours": 2,
        "quantiles": [0.1, 0.5, 0.9],
        "observation": {"now": {"interval_start_utc": "2026-03-08T06:00:00+00:00"}},
    }
    assert parsed is not None
    # Median is the middle quantile; the inner list is one value per hour.
    assert parsed["series"]["LZ_HOUSTON"]["values"][1] == [3.0, 4.0]
    assert missing is None
    assert set(SERIES) == {f"LZ_{zone}" for zone in LOAD_ZONES} | {"MCPC_ECRS", "MCPC_NSPIN"}


def test_grader_metrics_are_exact_on_a_hand_computed_forecast():
    """One hour, every series realized at 10.

    The forecast is P10=8, P50=13, P90=14. Median errors are all 3, so MAE and
    RMSE are 3. Pinball is 0.1*(10-8)=0.2, 0.5*(13-10)=1.5, and
    (1-0.9)*(14-10)=0.4. Coverage is 1 because 10 sits inside [8, 14]. The top
    10% of six equal prices is that price, and every median clears it, so spike
    precision and recall are 1.
    """
    from harness.forecaster import grade

    issued = "2026-03-08T06:00:00+00:00"
    frame = _hour("2026-03-08 06:00", price=10.0)

    class Flat:
        name = "flat"
        look_ahead = False

        def forecast(self, observation, *, horizon_hours, quantiles):
            return {
                "issued_at": observation["now"]["interval_start_utc"],
                "horizon_hours": horizon_hours,
                "series": {name: {"quantiles": list(quantiles), "values": [[8.0], [13.0], [14.0]]}
                           for name in SERIES},
            }

    report = grade([Flat()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    cell = report.scores[0].overall
    assert report.scores[0].name == "flat"
    assert cell.n == 6
    assert cell.mae == 3.0
    assert cell.rmse == 3.0
    # 0.1: outcome above the quantile, pinball = 0.1 * 2
    # 0.5: outcome below, pinball = 0.5 * 3
    # 0.9: outcome below, pinball = 0.1 * 4
    assert cell.pinball[0.1] == pytest.approx(0.2)
    assert cell.pinball[0.5] == pytest.approx(1.5)
    assert cell.pinball[0.9] == pytest.approx(0.4)
    assert cell.coverage == 1.0
    assert (cell.spike_precision, cell.spike_recall) == (1.0, 1.0)


def test_accuracy_splits_by_horizon_bucket_and_by_calm_versus_scarce():
    """Eight target hours, forecast median 0 everywhere, one issue at 06:00.

    Lead 0 (next hour) is scarce and realizes at 2. Leads 1 through 5 realize
    at 4. Leads 6 and 7 realize at 4 and 6, so the 6-24h MAE is 5. The scarce
    MAE is 2, and the calm MAE is (4*6 + 6) / 7.
    """
    from harness.forecaster import grade

    frame = _leads([2, 4, 4, 4, 4, 4, 4, 6], scarce_leads={0})

    report = grade([_Flat(0.0)], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, horizon_hours=8,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)
    score = report.scores[0]

    assert score.overall.mae == 4.0
    assert score.by_horizon["next_hour"].mae == 2.0
    assert score.by_horizon["h1_6"].mae == 4.0
    assert score.by_horizon["next_hour"].n == 6
    assert score.by_horizon["h1_6"].n == 30
    assert score.by_horizon["h6_24"].mae == 5.0
    assert score.by_regime["scarce"].mae == 2.0
    assert score.by_regime["calm"].mae == pytest.approx(30 / 7)
    assert score.by_horizon_regime["next_hour"]["calm"].n == 0
    assert score.by_horizon_regime["h1_6"]["calm"].mae == 4.0
    assert score.by_horizon_regime["h6_24"]["scarce"].n == 0


def test_an_hourly_target_is_the_mean_of_its_five_minute_prices():
    """Three copies of 0, 10, 20 and 30 average to 15. A forecast of 15 has no error."""
    from harness.forecaster import grade

    prices = [0, 0, 0, 10, 10, 10, 20, 20, 20, 30, 30, 30]
    index = pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC", name="interval_start_utc")
    frame = _indexed(index, prices, scarce=False)

    report = grade([_Flat(15.0)], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    cell = report.scores[0].overall
    assert cell.n == 6
    assert cell.mae == 0.0
    assert cell.rmse == 0.0
    assert cell.pinball[0.5] == 0.0


def test_a_forecaster_never_sees_prices_or_vintages_posted_after_the_decision(tmp_path):
    """The hour being forecast realizes at 10, and a later hour at 999.

    A vintage posted at 09:00Z carries 999. The prior hour realized at 4, and a
    vintage posted at 05:00Z carries 40. The observation may contain 4 and 40.
    It must not contain 10 or 999: those are the target and a future posting.
    """
    from harness.forecast import ForecastStore
    from harness.forecaster import grade

    seen = []

    class Recorder(_Flat):
        def forecast(self, observation, *, horizon_hours, quantiles):
            seen.append(observation)
            return super().forecast(observation, horizon_hours=horizon_hours, quantiles=quantiles)

    early = pd.Timestamp("2026-03-08 05:00", tz="UTC")
    decision = pd.Timestamp("2026-03-08 06:00", tz="UTC")
    later = pd.Timestamp("2026-03-08 07:00", tz="UTC")
    prior = _indexed(pd.date_range(early, periods=12, freq="5min", tz="UTC"), 4.0, scarce=False)
    target = _indexed(pd.date_range(decision, periods=12, freq="5min", tz="UTC"), 10.0, scarce=False)
    future = _indexed(pd.date_range(later, periods=12, freq="5min", tz="UTC"), 999.0, scarce=False)
    frame = pd.concat([prior, target, future])
    store = ForecastStore(tmp_path)
    store.write("dam_spp", dt.date(2026, 3, 8), pd.DataFrame({
        "posted_time": [pd.Timestamp("2026-03-08 05:00", tz="UTC"),
                        pd.Timestamp("2026-03-08 09:00", tz="UTC")],
        "valid_time": [decision, pd.Timestamp("2026-03-08 10:00", tz="UTC")],
        "series": ["LZ_HOUSTON", "LZ_HOUSTON"],
        "value": [40.0, 999.0],
        "in_use": pd.Series([pd.NA, pd.NA], dtype="boolean"),
    }))

    grade([Recorder()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
          market=lambda start, end: frame, forecasts=store, horizon_hours=1,
          quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    issued_obs = next(obs for obs in seen
                      if obs["now"]["interval_start_utc"].startswith("2026-03-08T06:00:00"))
    issued = pd.Timestamp(issued_obs["now"]["interval_start_utc"])
    blob = json.dumps(issued_obs)
    assert "999" not in blob
    assert issued_obs["now"]["lz_price"]["HOUSTON"] == 4.0
    realized = issued_obs["history"]["realized"]
    assert realized
    assert all(pd.Timestamp(row["valid_time"]) < issued for row in realized)
    assert any(row["prices"]["LZ_HOUSTON"] == 4.0 for row in realized)
    assert all(row["prices"]["LZ_HOUSTON"] not in (10.0, 999.0) for row in realized)
    posted = [pd.Timestamp(row["posted_time"]) for rows in issued_obs["forecasts"].values() for row in rows]
    assert posted
    assert all(moment <= issued for moment in posted)
    assert any(row["value"] == 40.0 for row in issued_obs["forecasts"]["dam_spp"])


def test_persistence_repeats_the_last_realized_price_before_the_decision(tmp_path):
    """Prior intervals are 4 and the target hour is 10, so the median error is 6."""
    from harness.forecaster import grade, persistence

    frame = pd.concat([
        _indexed(pd.date_range("2026-03-08 05:05", periods=11, freq="5min", tz="UTC"), 4.0, scarce=False),
        _indexed(pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC"), 10.0, scarce=False),
    ])
    report = grade([persistence()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    score = report.scores[0]
    assert score.name == "persistence"
    assert score.look_ahead is False
    assert score.overall.mae == 6.0
    assert score.overall.rmse == 6.0

    from harness.forecaster.report import publish

    text = publish(report, tmp_path)
    assert "persistence" in text
    assert "6.000" in text
    assert "calm" in text
    assert "regime.svg" in "\n".join(path.name for path in tmp_path.iterdir())
    saved = json.loads((tmp_path / "leaderboard.json").read_text())
    assert saved["forecasters"][0]["mae"] == 6.0
    assert "persistence" in (tmp_path / "mae.svg").read_text()
    assert "next_hour" in (tmp_path / "horizon.svg").read_text()


def test_dam_prices_are_the_forecast_when_that_vintage_was_already_posted(tmp_path):
    """DAM is 40 and the hour realizes at 10. Persistence would have said 4, so the error is 30."""
    from harness.forecast import ForecastStore
    from harness.forecaster import dam_as_forecast, grade

    frame = pd.concat([
        _indexed(pd.date_range("2026-03-08 05:05", periods=11, freq="5min", tz="UTC"), 4.0, scarce=False),
        _indexed(pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC"), 10.0, scarce=False),
    ])
    store = ForecastStore(tmp_path)
    posted = pd.Timestamp("2026-03-08 05:00", tz="UTC")
    valid = pd.Timestamp("2026-03-08 06:00", tz="UTC")
    store.write("dam_spp", dt.date(2026, 3, 8), _vintage(
        posted, valid, ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"], 40.0,
    ))
    store.write("dam_mcpc", dt.date(2026, 3, 8), _vintage(posted, valid, ["ECRS", "NSPIN"], 40.0))

    report = grade([dam_as_forecast()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, forecasts=store, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    assert report.scores[0].name == "dam"
    assert report.scores[0].overall.mae == 30.0


def test_rtd_indicative_covers_the_next_hour_and_later_hours_persist(tmp_path):
    """RTD points 1 and 3 average to 2 for the next hour (error 8 against 10).

    The following hour has no RTD, so it repeats the last realized price, 4
    (error 6). Overall MAE is 7.
    """
    from harness.forecast import ForecastStore
    from harness.forecaster import grade, rtd_indicative

    frame = pd.concat([
        _indexed(pd.date_range("2026-03-08 05:05", periods=11, freq="5min", tz="UTC"), 4.0, scarce=False),
        _indexed(pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC"), 10.0, scarce=False),
        _indexed(pd.date_range("2026-03-08 07:05", periods=11, freq="5min", tz="UTC"), 10.0, scarce=False),
    ])
    store = ForecastStore(tmp_path)
    posted = pd.Timestamp("2026-03-08 05:30", tz="UTC")
    store.write("rtd_lmp", dt.date(2026, 3, 8), pd.concat([
        _vintage(posted, pd.Timestamp("2026-03-08 06:00", tz="UTC"),
                 ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"], 1.0),
        _vintage(posted, pd.Timestamp("2026-03-08 06:30", tz="UTC"),
                 ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"], 3.0),
    ], ignore_index=True))
    store.write("rtd_mcpc", dt.date(2026, 3, 8), pd.concat([
        _vintage(posted, pd.Timestamp("2026-03-08 06:00", tz="UTC"), ["ECRS", "NSPIN"], 1.0),
        _vintage(posted, pd.Timestamp("2026-03-08 06:30", tz="UTC"), ["ECRS", "NSPIN"], 3.0),
    ], ignore_index=True))

    report = grade([rtd_indicative()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, forecasts=store, horizon_hours=2,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    score = report.scores[0]
    assert score.name == "rtd"
    assert score.by_horizon["next_hour"].mae == 8.0
    assert score.by_horizon["h1_6"].mae == 6.0
    assert score.overall.mae == 7.0


def test_net_load_regression_fits_only_features_known_before_the_decision(tmp_path):
    """price = 1 + 2*net_load + 3*outage on three past hours, all quantiles at the fit.

    The decision hour's features are net load 2 and outage 1, so the forecast is
    8. The hour realizes at 10 (MAE 2). A later vintage that sets the same hour's
    load to 100 is posted after the decision and must not move the forecast.
    """
    from harness.forecast import ForecastStore
    from harness.forecaster import grade, net_load

    hours = {
        "2026-03-08 03:00": (0.0, 0.0, 1.0),
        "2026-03-08 04:00": (1.0, 0.0, 3.0),
        "2026-03-08 05:00": (0.0, 1.0, 4.0),
        "2026-03-08 06:00": (2.0, 1.0, 10.0),
    }
    frames = []
    for stamp, (load, _outage, price) in hours.items():
        start = pd.Timestamp(stamp, tz="UTC")
        if stamp.endswith("06:00"):
            index = pd.date_range(start, periods=12, freq="5min", tz="UTC")
        else:
            index = pd.date_range(start + pd.Timedelta(minutes=5), periods=11, freq="5min", tz="UTC")
        frames.append(_indexed(index, price, scarce=False))
    store = _net_load_store(tmp_path, hours)

    report = grade([net_load()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: pd.concat(frames), forecasts=store, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    cell = report.scores[0].overall
    assert report.scores[0].name == "net_load"
    assert report.scores[0].look_ahead is False
    assert cell.mae == 2.0
    assert cell.pinball[0.5] == 1.0


def test_the_look_ahead_oracle_scores_zero_without_the_observation_containing_the_answer():
    """P10/P50/P90 of realized prices: 0, 10, 20, 30 average to 15, and every error is 0."""
    from harness.forecaster import grade, oracle, persistence

    prices = [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 30.0, 30.0, 30.0]
    index = pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC")
    frame = _indexed(index, prices, scarce=False)
    seen = []

    class RecordingOracle:
        name = oracle(frame).name
        look_ahead = True

        def forecast(self, observation, *, horizon_hours, quantiles):
            seen.append(observation)
            return oracle(frame).forecast(observation, horizon_hours=horizon_hours, quantiles=quantiles)

    report = grade([RecordingOracle(), persistence()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                   market=lambda start, end: frame, horizon_hours=1,
                   quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    perfect, naive = report.scores
    assert perfect.look_ahead is True
    assert "look-ahead" in perfect.name
    assert perfect.overall.mae == 0.0
    assert perfect.overall.rmse == 0.0
    assert perfect.overall.pinball == {0.1: 0.0, 0.5: 0.0, 0.9: 0.0}
    assert seen[0]["now"]["lz_price"]["HOUSTON"] is None
    assert seen[0]["history"]["realized"] == []
    assert naive.overall.mae == 15.0
    assert naive.look_ahead is False


def test_five_minute_steps_in_the_first_hour_score_each_interval():
    """The first price is 9 and the other eleven are 0. Forecasting those exactly
    has no error. Treating the twelve steps as one hour would not.
    """
    from harness.forecaster import grade

    index = pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC")
    frame = _indexed(index, [9.0, *([0.0] * 11)], scarce=False)

    class FiveMinute:
        name = "five"
        look_ahead = False

        def forecast(self, observation, *, horizon_hours, quantiles):
            row = [9.0, *([0.0] * 11)]
            return {
                "issued_at": observation["now"]["interval_start_utc"],
                "horizon_hours": horizon_hours,
                "series": {name: {"quantiles": list(quantiles), "values": [row[:] for _ in quantiles]}
                           for name in SERIES},
            }

    cell = grade([FiveMinute()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                 market=lambda start, end: frame, horizon_hours=1,
                 quantiles=(0.1, 0.5, 0.9), spike_top=0.1).scores[0].overall
    assert cell.n == 72
    assert cell.mae == 0.0


def test_harness_forecast_prints_a_leaderboard_and_the_oracle_has_zero_error(market_store, tmp_path, capsys):
    from conftest import SPRING_FORWARD
    from harness.cli import main

    out = tmp_path / "board"
    code = main(["forecast", "--forecaster", "persistence", "--forecaster", "oracle",
                 "--start", SPRING_FORWARD.isoformat(), "--end", SPRING_FORWARD.isoformat(),
                 "--market-dir", str(market_store), "--horizon", "1", "--out", str(out)])

    assert code == 0
    printed = capsys.readouterr().out
    assert "persistence" in printed
    assert "look-ahead" in printed
    assert "0.000" in printed
    saved = json.loads((out / "leaderboard.json").read_text())
    rows = {row["name"]: row for row in saved["forecasters"]}
    assert rows["oracle (look-ahead)"]["look_ahead"] is True
    assert rows["oracle (look-ahead)"]["mae"] == 0.0
    assert rows["persistence"]["look_ahead"] is False
    assert rows["persistence"]["mae"] > 0
    assert "persistence" in (out / "mae.svg").read_text()
    assert "pinball" in (out / "pinball.svg").read_text().lower() or "Pinball" in (out / "pinball.svg").read_text()


def test_a_run_stores_one_hourly_forecast_and_leaves_policy_history_empty(market_store):
    from conftest import SPRING_FORWARD, Recorder, fleet_scenario, recorded
    from harness import run
    from harness.forecaster import persistence

    calls = []

    class Counting:
        name = "counting"
        look_ahead = False

        def forecast(self, observation, *, horizon_hours, quantiles):
            calls.append(observation)
            return persistence().forecast(observation, horizon_hours=horizon_hours, quantiles=quantiles)

    policy = Recorder()
    run(policy, fleet_scenario(), SPRING_FORWARD, SPRING_FORWARD, seed=1,
        market=recorded(market_store), forecaster=Counting(), price_horizon_hours=1)

    assert len(policy.seen) == 276
    assert all(obs["history"] == {} for obs in policy.seen)
    assert policy.seen[0]["forecaster"]["horizon_hours"] == 1
    # 2026-03-08 is the spring-forward day: 23 hours, one forecast each.
    assert len(calls) == 23
    for view in calls:
        issued = pd.Timestamp(view["now"]["interval_start_utc"])
        assert all(pd.Timestamp(row["valid_time"]) < issued for row in view["history"]["realized"])
    eight = next(obs for obs in policy.seen if obs["now"]["interval_start_utc"] == "2026-03-08T08:00:00+00:00")
    view = next(obs for obs in calls if obs["now"]["interval_start_utc"] == "2026-03-08T08:00:00+00:00")
    assert eight["now"]["lz_price"]["HOUSTON"] == 37.24
    assert view["now"]["lz_price"]["HOUSTON"] != 37.24


def test_the_example_persistence_forecaster_matches_the_builtin():
    import sys
    from pathlib import Path

    from harness.forecaster import ExternalForecaster, grade, persistence

    example = Path(__file__).resolve().parents[1] / "examples" / "persistence_forecaster.py"
    frame = pd.concat([
        _indexed(pd.date_range("2026-03-08 05:05", periods=11, freq="5min", tz="UTC"), 4.0, scarce=False),
        _indexed(pd.date_range("2026-03-08 06:00", periods=12, freq="5min", tz="UTC"), 10.0, scarce=False),
    ])
    with ExternalForecaster([sys.executable, str(example)]) as external:
        report = grade([external, persistence()], dt.date(2026, 3, 8), dt.date(2026, 3, 8),
                       market=lambda start, end: frame, horizon_hours=1,
                       quantiles=(0.1, 0.5, 0.9), spike_top=0.1)

    external_score, builtin = report.scores
    assert external_score.name == "persistence"
    assert external_score.overall.mae == builtin.overall.mae == 6.0


class _Flat:
    """Every series and quantile holds one number for the first hour."""

    name = "flat"
    look_ahead = False

    def __init__(self, value=0.0):
        self.value = value

    def forecast(self, observation, *, horizon_hours, quantiles):
        row = [float(self.value)] * horizon_hours
        return {
            "issued_at": observation["now"]["interval_start_utc"],
            "horizon_hours": horizon_hours,
            "series": {name: {"quantiles": list(quantiles), "values": [row[:] for _ in quantiles]}
                       for name in SERIES},
        }


def _hour(start, price, *, periods=12, scarce=False):
    index = pd.date_range(start, periods=periods, freq="5min", tz="UTC", name="interval_start_utc")
    return _indexed(index, price, scarce=scarce)


def _leads(prices, *, scarce_leads):
    """One issue hour at 06:00Z, then later hours with no on-the-hour row."""
    frames = []
    start = pd.Timestamp("2026-03-08 06:00", tz="UTC")
    for lead, price in enumerate(prices):
        hour = start + pd.Timedelta(hours=lead)
        if lead == 0:
            index = pd.date_range(hour, periods=12, freq="5min", tz="UTC")
        else:
            index = pd.date_range(hour + pd.Timedelta(minutes=5), periods=11, freq="5min", tz="UTC")
        frames.append(_indexed(index, price, scarce=lead in scarce_leads))
    return pd.concat(frames)


def _vintage(posted, valid, series, value):
    return pd.DataFrame({
        "posted_time": posted,
        "valid_time": valid,
        "series": list(series),
        "value": value,
        "in_use": pd.Series([pd.NA] * len(series), dtype="boolean"),
    })


def _net_load_store(root, hours):
    from harness.forecast import ForecastStore

    posted = pd.Timestamp("2026-03-08 02:00", tz="UTC")
    leaked = pd.Timestamp("2026-03-08 07:00", tz="UTC")
    load, wind, solar, outage = [], [], [], []
    for stamp, (mw, offline, _price) in hours.items():
        valid = pd.Timestamp(stamp, tz="UTC")
        load.append(_point(posted, valid, "A3|system", mw, True))
        wind.append(_point(posted, valid, "stwpf|system", 0.0, None))
        solar.append(_point(posted, valid, "stppf|system", 0.0, None))
        outage.append(_point(posted, valid, "total_resource|houston", offline, None))
    load.append(_point(leaked, pd.Timestamp("2026-03-08 06:00", tz="UTC"), "A3|system", 100.0, True))
    store = ForecastStore(root)
    store.write("load_by_model_zone", dt.date(2026, 3, 8), pd.concat(load, ignore_index=True))
    store.write("wind_system", dt.date(2026, 3, 8), pd.concat(wind, ignore_index=True))
    store.write("solar_system", dt.date(2026, 3, 8), pd.concat(solar, ignore_index=True))
    store.write("outage_capacity", dt.date(2026, 3, 8), pd.concat(outage, ignore_index=True))
    return store


def _point(posted, valid, series, value, in_use):
    flag = pd.NA if in_use is None else bool(in_use)
    return pd.DataFrame({
        "posted_time": [posted],
        "valid_time": [valid],
        "series": [series],
        "value": [float(value)],
        "in_use": pd.Series([flag], dtype="boolean"),
    })


def _indexed(index, price, *, scarce):
    index = pd.DatetimeIndex(index, name="interval_start_utc")
    columns = {
        "interval_start_cpt": index.tz_convert("America/Chicago"),
        "operating_day": pd.Timestamp("2026-03-08"),
        "scarce_ecrs": scarce,
        "scarce_nspin": scarce,
    }
    for suffix in ("houston", "north", "south", "west"):
        columns[f"lz_spp_{suffix}"] = price
    columns["rt_mcpc_5m_ecrs"] = price
    columns["rt_mcpc_5m_nspin"] = price
    return pd.DataFrame(columns, index=index)
