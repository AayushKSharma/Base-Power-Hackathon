"""Reference policies: fixed haircut, newsvendors, and a reliability target."""

import datetime as dt
import sys
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from conftest import SPRING_FORWARD, fleet_scenario, recorded
from harness import ConstantHaircut, ExternalPolicy, run
from harness.market.catalog import CPT
from harness.observation import Observation
from harness.market.normalize import day_grid
from harness.policy import builtin_policy
from harness.reference import Belief, CorrelatedNewsvendor, IndependentNewsvendor, ReliabilityTarget
from harness.scenario import parse_scenario


def fleet_observation(*, ecrs_price=5.0, nonspin_price=5.0, houston=0.0, north=0.0,
                      south=0.0, west=0.0, scarce=False):
    """Four online homes, each with 8 kW of ECRS and 2 kW of Non-Spin."""
    return {
        "now": {
            "interval_start_utc": "2026-03-08T06:00:00+00:00",
            "interval_start_cpt": "2026-03-08T01:00:00-05:00",
            "rt_mcpc": {"ECRS": ecrs_price, "NONSPIN": nonspin_price},
            "lz_price": {"HOUSTON": houston, "NORTH": north, "SOUTH": south, "WEST": west},
            "scarce": {"ECRS": scarce, "NONSPIN": scarce},
        },
        "history": {},
        "forecasts": {},
        "forecaster": {},
        "fleet": {"regions": [{
            "region_id": 0,
            "homes": 4,
            "homes_online": 4,
            "homes_stale": 0,
            "homes_backup": 0,
            "energy_above_floor_kwh": 32.0,
            "inverter_kw": 40.0,
            "capability_kw": {"ECRS": 32.0, "NONSPIN": 8.0},
        }]},
        "products": {
            "ECRS": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
            "NONSPIN": {"duration_h": 4.0, "cap_mw": 100.0, "cap_share": 0.9},
        },
    }


# Four identical homes survive a window independently with probability
# (1 - 0.5) ** duration. ECRS is 1 hour, so each home survives with probability
# 1/2. Deliverable MW is 0.008 times a Binomial(4, 1/2) count:
#   P(0) = 1/16, P(<=1) = 5/16, P(<=2) = 11/16.
# Critical fractile alpha = 5 / (1 * (0 * duration + 10)) = 1/2,
# and 11/16 is the first cumulative probability at or above 1/2, so 2 homes:
# 0.016 MW. Non-Spin is 4 hours, so a home survives with probability 1/16.
# P(0 homes survive) = (15/16) ** 4 > 1/2, so the report is 0 MW.
HALF_DROPOUT = Belief(
    deployment_calm=1.0,
    deployment_scarce=1.0,
    compliance_per_mw=10.0,
    home_dropout_per_h=0.5,
    region_outage_per_h=0.0,
    scarcity_stress=1.0,
)


def test_independent_newsvendor_reports_the_critical_fractile_of_independent_dropouts():
    reported = IndependentNewsvendor(belief=HALF_DROPOUT).decide(fleet_observation())

    assert reported["ECRS"] == pytest.approx(0.016)
    assert reported["NONSPIN"] == pytest.approx(0.0)


def test_independent_newsvendor_keeps_each_regions_own_megawatts():
    # No compliance cost, so alpha is infinite and every online home is reported.
    # Two homes at 8 kW and two at 16 kW: 0.016 + 0.032 = 0.048 MW, not 4 * 8 kW.
    belief = Belief(
        deployment_calm=1.0, compliance_per_mw=0.0, home_dropout_per_h=0.5,
        region_outage_per_h=0.0, scarcity_stress=1.0,
    )
    observation = fleet_observation()
    observation["fleet"]["regions"] = [
        {**observation["fleet"]["regions"][0], "homes": 2, "homes_online": 2,
         "capability_kw": {"ECRS": 16.0, "NONSPIN": 4.0}},
        {**observation["fleet"]["regions"][0], "region_id": 1, "homes": 2, "homes_online": 2,
         "capability_kw": {"ECRS": 32.0, "NONSPIN": 8.0}},
    ]

    reported = IndependentNewsvendor(belief=belief).decide(observation)

    assert reported["ECRS"] == pytest.approx(0.048)


def test_belief_parameters_are_configurable_and_recorded_on_the_scorecard(market_store):
    belief = Belief(compliance_per_mw=42.0, home_dropout_per_h=0.2)

    card = run(IndependentNewsvendor(belief=belief), fleet_scenario(), SPRING_FORWARD, SPRING_FORWARD,
               seed=1, market=recorded(market_store)).scorecards["P50"]

    recorded_belief = card.to_dict()["beliefs"]
    assert recorded_belief["compliance_per_mw"] == 42.0
    assert recorded_belief["home_dropout_per_h"] == 0.2
    assert recorded_belief["deployment_calm"] == 0.02
    assert recorded_belief["deployment_scarce"] == 0.30
    assert recorded_belief["region_outage_per_h"] == 0.005
    assert recorded_belief["scarcity_stress"] == 4.0


def test_constant_haircut_keeps_its_name_and_records_baseline_beliefs(market_store):
    card = run(ConstantHaircut(fraction=0.9), fleet_scenario(), SPRING_FORWARD, SPRING_FORWARD,
               seed=1, market=recorded(market_store)).scorecards["P50"]

    assert card.policy == "constant_haircut(fraction=0.9)"
    assert card.to_dict()["beliefs"]["home_dropout_per_h"] == 0.01
    assert card.to_dict()["beliefs"]["compliance_per_mw"] == 500.0
    assert card.to_dict()["beliefs"]["deployment_calm"] == 0.02


def test_built_in_policies_take_belief_parameters_on_a_run(market_store):
    scenario = fleet_scenario()
    policy = builtin_policy("independent_newsvendor", {"compliance_per_mw": "42", "home_dropout_per_h": "0.2"}, scenario)

    card = run(policy, scenario, SPRING_FORWARD, SPRING_FORWARD, seed=1,
               market=recorded(market_store)).scorecards["P50"]

    assert card.policy == "independent_newsvendor"
    assert card.to_dict()["beliefs"]["compliance_per_mw"] == 42.0
    assert card.to_dict()["beliefs"]["home_dropout_per_h"] == 0.2


# Scarce all day, with regional outages likely over a one-hour window.
# The belief matches those rates. A 90% haircut ignores the correlation.
REGIONAL = Belief(
    deployment_calm=1.0, deployment_scarce=1.0, compliance_per_mw=200.0,
    home_dropout_per_h=0.05, home_dropout_min=60.0,
    region_outage_per_h=0.5, region_outage_min=120.0, scarcity_stress=1.0,
)


def test_correlated_monte_carlo_beats_fixed_haircut_when_regions_drop_together():
    day = dt.date(2026, 6, 15)
    scenario = _regional_scenario()
    market = lambda start, end: _flat_market(start, end, mcpc=40.0, lam=800.0, scarce=True)

    haircut = run(ConstantHaircut(fraction=0.9), scenario, day, day, seed=3, market=market)
    correlated = run(CorrelatedNewsvendor(samples=64, belief=REGIONAL), scenario, day, day, seed=3, market=market)
    independent = run(IndependentNewsvendor(belief=REGIONAL), scenario, day, day, seed=3, market=market)

    def net(result):
        totals = result.scorecards["stochastic"].totals
        return totals["ECRS"].net + totals["NONSPIN"].net

    correlated_net, haircut_net, independent_net = net(correlated), net(haircut), net(independent)
    assert correlated_net > haircut_net
    # The independent policy ignores regional outages, so its net is the one
    # that does not get credit for the correlation.
    assert independent_net < correlated_net


def test_monte_carlo_samples_repeat_for_a_seed_and_follow_the_generator_tree():
    day = dt.date(2026, 6, 15)
    nxt = day + dt.timedelta(days=1)
    scenario = _regional_scenario()
    market = lambda start, end: _flat_market(start, end, mcpc=40.0, lam=800.0, scarce=True)

    def reported(seed, start, end):
        result = run(CorrelatedNewsvendor(samples=32, belief=REGIONAL), scenario, start, end,
                     seed=seed, market=market)
        rows = result.intervals
        rows = rows[(rows["product"] == "ECRS") & (rows["fleet_case"] == "stochastic")]
        return rows.sort_values("interval_start_utc")

    first = reported(3, day, nxt)
    again = reported(3, day, nxt)
    assert list(again["reported_mw"]) == list(first["reported_mw"])
    assert list(reported(9, day, day)["reported_mw"]) != list(reported(3, day, day)["reported_mw"])
    alone = reported(3, nxt, nxt)
    inside = first[pd.to_datetime(first["operating_day"]).dt.date == nxt]
    assert list(inside["reported_mw"]) == list(alone["reported_mw"])


def test_each_reference_policy_matches_through_the_external_protocol():
    day = dt.date(2026, 6, 15)
    scenario = _regional_scenario()
    market = lambda start, end: _flat_market(start, end, mcpc=40.0, lam=800.0, scarce=True, hours=1)
    script = Path(__file__).resolve().parents[1] / "examples" / "reference_policy.py"
    custom = Belief(compliance_per_mw=42.0, region_outage_per_h=0.5)
    cases = (
        (ConstantHaircut(fraction=0.5, belief=custom),
         ["--policy", "constant_haircut", "--fraction", "0.5", "--compliance_per_mw", "42",
          "--region_outage_per_h", "0.5"]),
        (IndependentNewsvendor(belief=custom),
         ["--policy", "independent_newsvendor", "--compliance_per_mw", "42", "--region_outage_per_h", "0.5"]),
        (CorrelatedNewsvendor(samples=16, belief=custom),
         ["--policy", "correlated_newsvendor", "--samples", "16", "--compliance_per_mw", "42",
          "--region_outage_per_h", "0.5"]),
        (ReliabilityTarget(epsilon=0.1, belief=custom),
         ["--policy", "reliability_target", "--epsilon", "0.1", "--compliance_per_mw", "42",
          "--region_outage_per_h", "0.5"]),
    )

    for policy, args in cases:
        internal = run(policy, scenario, day, day, seed=3, market=market).scorecards
        command = [sys.executable, str(script), *args]
        with ExternalPolicy(command, scenario.products, timeout_s=5) as external:
            outside = run(external, scenario, day, day, seed=3, market=market).scorecards
        assert outside == internal


def test_reliability_target_shortfall_frequency_tracks_epsilon_when_belief_matches_truth():
    # The belief is the scenario's failure model: independent home dropouts,
    # no regional outages. Over twelve days the share of intervals where
    # deliverable MW falls short of K should sit near each epsilon.
    start, end = dt.date(2026, 4, 1), dt.date(2026, 4, 12)
    scenario = _matched_belief_scenario()
    belief = Belief(
        deployment_calm=0.0, deployment_scarce=0.0, compliance_per_mw=0.0,
        home_dropout_per_h=0.4, home_dropout_min=60.0,
        region_outage_per_h=0.0, region_outage_min=120.0, scarcity_stress=1.0,
    )
    market = lambda first, last: _flat_market(first, last, mcpc=10.0, lam=25.0, scarce=False)

    rates = {}
    for epsilon in (0.05, 0.10, 0.20):
        card = run(ReliabilityTarget(epsilon=epsilon, belief=belief), scenario, start, end,
                   seed=1, market=market).scorecards["stochastic"]
        rates[epsilon] = card.totals["ECRS"].overstatement_rate

    assert rates[0.05] < rates[0.10] < rates[0.20]
    # The count of surviving homes is discrete, so the chance constraint lands
    # on a probability at or just under epsilon. Twelve days pin the noise down.
    for epsilon, rate in rates.items():
        assert abs(rate - epsilon) < 0.05


def test_reliability_target_keeps_the_shortfall_probability_within_epsilon():
    # P(fewer than one home delivers ECRS) = 1/16 <= 0.10, and
    # P(fewer than two) = 5/16 > 0.10, so the report is one home: 0.008 MW.
    # Non-Spin's chance of zero surviving homes is (15/16)**4 > 0.10, so 0 MW.
    reported = ReliabilityTarget(epsilon=0.1, belief=HALF_DROPOUT).decide(fleet_observation())

    assert reported["ECRS"] == pytest.approx(0.008)
    assert reported["NONSPIN"] == pytest.approx(0.0)


def test_the_load_zone_price_is_weighted_by_the_beliefs_zone_shares():
    # Houston $0 and North $20, weights 1/4 and 3/4, so λ = $15.
    # ECRS alpha = 5 / (15 * 1 + 10) = 0.2. P(0 homes) = 1/16 and
    # P(1 or fewer) = 5/16, so the critical fractile is one home: 0.008 MW.
    # Using Houston alone would keep alpha at 1/2 and report 0.016 MW.
    belief = Belief(
        deployment_calm=1.0, deployment_scarce=1.0, compliance_per_mw=10.0,
        home_dropout_per_h=0.5, region_outage_per_h=0.0, scarcity_stress=1.0,
        zone_houston=0.25, zone_north=0.75,
    )

    reported = IndependentNewsvendor(belief=belief).decide(
        fleet_observation(houston=0.0, north=20.0))

    assert reported["ECRS"] == pytest.approx(0.008)


def test_a_forecaster_price_replaces_the_current_row_when_one_is_present():
    # Median of the forecaster's first step is $20/MW-h, so ECRS alpha =
    # 20 / 10 = 2. That is above 1, so every online home is reported: 0.032 MW.
    # The current row still says $5, which would have reported 0.016 MW.
    observation = fleet_observation()
    observation["forecaster"] = _forecaster(ecrs=20.0, houston=0.0)

    reported = IndependentNewsvendor(belief=HALF_DROPOUT).decide(observation)

    assert reported["ECRS"] == pytest.approx(0.032)


def test_a_forecast_posted_before_the_decision_replaces_the_current_price():
    # Day-ahead ECRS MCPC for this hour was posted at 05:00 and is $20.
    # alpha = 2, so the report is the full 0.032 MW, not the $5 row's 0.016.
    observation = fleet_observation()
    observation["forecasts"] = {
        "dam_mcpc": [{
            "posted_time": "2026-03-08T05:00:00+00:00",
            "valid_time": "2026-03-08T06:00:00+00:00",
            "series": "ECRS",
            "value": 20.0,
        }],
    }

    reported = IndependentNewsvendor(belief=HALF_DROPOUT).decide(observation)

    assert reported["ECRS"] == pytest.approx(0.032)


class _FutureValue(dict):
    """A mapping whose future payload raises if a policy reads it."""

    def __getitem__(self, key):
        if key in {"value", "prices"}:
            raise AssertionError(f"policy read future data ({key})")
        return dict.__getitem__(self, key)


class _NoLookAhead(dict):
    def __getitem__(self, key):
        if key == "lookahead":
            raise AssertionError("policy read lookahead")
        return dict.__getitem__(self, key)


def test_reference_policies_do_not_read_prices_posted_after_the_decision():
    observation = _NoLookAhead(fleet_observation())
    observation["lookahead"] = {"rt_mcpc": {"ECRS": 20.0, "NONSPIN": 20.0}}
    observation["history"] = {"realized": [_FutureValue({
        "valid_time": "2026-03-08T07:00:00+00:00",
        "prices": {"MCPC_ECRS": 20.0},
    })]}
    observation["forecasts"] = {"dam_mcpc": [_FutureValue({
        "posted_time": "2026-03-08T07:00:00+00:00",
        "valid_time": "2026-03-08T06:00:00+00:00",
        "series": "ECRS",
        "value": 20.0,
    })]}

    policies = (
        IndependentNewsvendor(belief=HALF_DROPOUT),
        ReliabilityTarget(epsilon=0.1, belief=HALF_DROPOUT),
        CorrelatedNewsvendor(samples=4, belief=HALF_DROPOUT),
        ConstantHaircut(fraction=0.9),
    )
    seen = cast(Observation, observation)
    reports = [policy.decide(seen) for policy in policies]

    # $5 from the current row, not the $20 posted an hour later.
    assert reports[0]["ECRS"] == pytest.approx(0.016)
    for report in reports:
        assert set(report) == {"ECRS", "NONSPIN"}


def _regional_scenario():
    """Forty homes, four regions. The battery stays inverter-limited for a day."""
    data = {
        "seed": 7,
        "fleet": {
            "homes": 40, "regions": 4, "battery_kwh": 5000, "inverter_kw": 10,
            "backup_floor": 0.2, "soc": {"fixed": 0.6}, "telemetry_stale_s": 0,
            "state": "stochastic",
            "quantile_mock": {"shares": {q: 1.0 for q in ("P10", "P25", "P50", "P75", "P90")},
                              "policy_view": "per_case", "typical": "P50"},
        },
        "failures": {
            "home_dropout_per_h": 0.05, "home_dropout_min": 60.0,
            "region_outage_per_h": 0.5, "region_outage_min": 120.0,
            "scarcity_stress": 1.0, "forced_region_outages": [],
        },
        "deployments": {"calm": 1.0, "scarce": 1.0, "refill_kw": 0.0, "forced": []},
        "scoring": {
            "preset": "energy", "load_zone": "HOUSTON", "compliance_per_mw": 200.0,
            "spd_per_mwh": 0.0, "exceedance_mw": [0.0, 1.0], "tolerance_mw": [1.0],
        },
        "products": {
            "ECRS": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
            "NONSPIN": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
        },
    }
    return parse_scenario(data, name="regional")


def _matched_belief_scenario():
    return parse_scenario({
        "seed": 1,
        "fleet": {
            "homes": 200, "regions": 10, "battery_kwh": 20, "inverter_kw": 10,
            "backup_floor": 0.2, "soc": {"fixed": 0.6}, "telemetry_stale_s": 0,
            "state": "stochastic",
            "quantile_mock": {"shares": {q: 1.0 for q in ("P10", "P25", "P50", "P75", "P90")},
                              "policy_view": "per_case", "typical": "P50"},
        },
        "failures": {
            "home_dropout_per_h": 0.4, "home_dropout_min": 60.0,
            "region_outage_per_h": 0.0, "region_outage_min": 120.0,
            "scarcity_stress": 1.0, "forced_region_outages": [],
        },
        "deployments": {"calm": 0.0, "scarce": 0.0, "refill_kw": 0.0, "forced": []},
        "scoring": {
            "preset": "energy", "load_zone": "HOUSTON", "compliance_per_mw": 0.0,
            "spd_per_mwh": 0.0, "exceedance_mw": [1.0], "tolerance_mw": [1.0],
        },
        "products": {
            "ECRS": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
            "NONSPIN": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
        },
    }, name="matched")


def _flat_market(start: dt.date, end: dt.date, *, mcpc: float, lam: float, scarce: bool,
                 hours: int | None = None) -> pd.DataFrame:
    frames = []
    day = start
    while day <= end:
        grid = day_grid(day)
        if hours is not None:
            grid = grid[: hours * 12]
        frames.append(pd.DataFrame({
            "interval_start_cpt": grid.tz_convert(CPT),
            "operating_day": pd.Timestamp(day),
            "rt_mcpc_5m_ecrs": mcpc,
            "rt_mcpc_5m_nspin": mcpc,
            "rt_mcpc_15m_ecrs": mcpc,
            "rt_mcpc_15m_nspin": mcpc,
            "q_rt_mcpc_15m_ecrs": "ok",
            "q_rt_mcpc_15m_nspin": "ok",
            "lz_spp_houston": lam,
            "lz_spp_north": lam,
            "lz_spp_south": lam,
            "lz_spp_west": lam,
            "scarce_ecrs": scarce,
            "scarce_nspin": scarce,
        }, index=grid))
        day += dt.timedelta(days=1)
    return pd.concat(frames)


def _forecaster(*, ecrs, houston):
    quantiles = [0.1, 0.5, 0.9]
    def series(value):
        return {"quantiles": quantiles, "values": [[value], [value], [value]]}

    return {
        "issued_at": "2026-03-08T06:00:00+00:00",
        "horizon_hours": 1,
        "series": {
            "LZ_HOUSTON": series(houston),
            "LZ_NORTH": series(0.0),
            "LZ_SOUTH": series(0.0),
            "LZ_WEST": series(0.0),
            "MCPC_ECRS": series(ecrs),
            "MCPC_NSPIN": series(ecrs),
        },
    }
