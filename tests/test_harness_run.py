import json

import pytest

from conftest import (
    AFTER_SPRING_FORWARD,
    CALM,
    MISSING_15MIN,
    SPRING_FORWARD,
    Recorder,
    fleet_scenario,
    recorded,
    settlement_prices,
)
from harness import ConstantHaircut, Scorecard, run


def stochastic(seed=7):
    return fleet_scenario(state="stochastic", failures={**CALM, "scarcity_stress": 4.0},
                          soc={"beta": [6, 4]}, homes=200, regions=10, seed=seed)


def test_reserve_revenue_is_award_times_settlement_price_times_interval_length(market_store):
    # The fleet can sustain 0.8 MW of ECRS and 0.2 MW of Non-Spin; the policy reports half.
    card = run(ConstantHaircut(fraction=0.5), fleet_scenario(), SPRING_FORWARD, SPRING_FORWARD, seed=1,
               market=settlement_prices(market_store, ecrs=12.0, nonspin=3.0)).scorecards["P50"]

    # 0.4 MW of ECRS at $12/MW-h and 0.1 MW of Non-Spin at $3/MW-h, for 23 hours.
    assert card.totals["ECRS"].intervals == 276
    assert card.totals["ECRS"].award_mw_h == pytest.approx(0.4 * 23)
    assert card.totals["ECRS"].revenue == pytest.approx(0.4 * 12 * 23)
    assert card.totals["NONSPIN"].revenue == pytest.approx(0.1 * 3 * 23)


def test_awards_are_limited_to_the_cap_share(market_store):
    sc = fleet_scenario(cap_mw=0.5, cap_share=0.9)

    card = run(ConstantHaircut(fraction=1.0), sc, SPRING_FORWARD, SPRING_FORWARD, seed=1,
               market=settlement_prices(market_store, ecrs=12.0, nonspin=3.0)).scorecards["P50"]

    # Reports 0.8 MW, but one QSE may hold at most 90% of the 0.5 MW cap.
    ecrs = card.totals["ECRS"]
    assert ecrs.reported_mw_h == pytest.approx(0.8 * 23)
    assert ecrs.award_mw_h == pytest.approx(0.45 * 23)
    assert ecrs.revenue == pytest.approx(0.45 * 12 * 23)


def test_intervals_without_a_settlement_price_earn_nothing_and_are_counted(market_store):
    card = run(ConstantHaircut(fraction=0.9), fleet_scenario(), MISSING_15MIN, MISSING_15MIN, seed=1,
               market=recorded(market_store)).scorecards["P50"]

    ecrs = card.totals["ECRS"]
    assert (ecrs.intervals, ecrs.skipped) == (288, 288)
    assert (ecrs.revenue, ecrs.revenue_given_up) == (0.0, 0.0)


def test_the_policy_observes_the_market_row_fleet_and_product_rules(market_store):
    policy = Recorder()

    run(policy, fleet_scenario(shares={"P10": 1.0, "P25": 1.0, "P50": 1.0, "P75": 1.0, "P90": 1.0}),
        SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    assert len(policy.seen) == 276 * 5  # once per interval for each quantile
    obs = next(o for o in policy.seen if o["now"]["interval_start_utc"] == "2026-03-08T08:00:00+00:00")
    assert set(obs) == {"now", "history", "forecasts", "forecaster", "fleet", "products"}
    # NP6-332-CD SCED run 03:00:22 CDT; NP6-905-CD HE04 interval 1.
    assert obs["now"]["interval_start_cpt"] == "2026-03-08T03:00:00-05:00"
    assert obs["now"]["rt_mcpc"] == {"ECRS": 0.75, "NONSPIN": 3.0}
    assert obs["now"]["lz_price"] == {"HOUSTON": 37.24, "NORTH": 42.59, "SOUTH": 34.23, "WEST": 65.62}
    assert set(obs["now"]["scarce"]) == {"ECRS", "NONSPIN"}
    assert obs["products"] == {"ECRS": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
                               "NONSPIN": {"duration_h": 4.0, "cap_mw": 100.0, "cap_share": 0.9}}
    region = obs["fleet"]["regions"][0]
    assert (region["homes"], region["homes_online"], region["homes_stale"], region["homes_backup"]) == (25, 25, 0, 0)
    assert region["capability_kw"] == {"ECRS": pytest.approx(25 * 8), "NONSPIN": pytest.approx(25 * 2)}
    # Filled by later slices; present now so the interface doesn't change.
    assert obs["history"] == obs["forecasts"] == obs["forecaster"] == {}
    # External policies receive it as JSON.
    assert json.loads(json.dumps(obs)) == obs


def test_a_date_range_run_combines_independent_day_runs(market_store):
    policy = ConstantHaircut(fraction=0.9)
    days = (SPRING_FORWARD, AFTER_SPRING_FORWARD)

    whole = run(policy, stochastic(), *days, seed=3, market=recorded(market_store)).scorecards
    single = [run(policy, stochastic(), d, d, seed=3, market=recorded(market_store)).scorecards
              for d in days]

    card = whole["stochastic"]
    assert card == Scorecard.combine(s["stochastic"] for s in single)
    assert [d.day for d in card.days] == list(days)
    assert card.totals["ECRS"].intervals == 276 + 288
    assert card.totals["ECRS"].oversold_mw_h == pytest.approx(
        sum(s["stochastic"].totals["ECRS"].oversold_mw_h for s in single))


def test_the_same_seed_gives_an_identical_scorecard(market_store):
    policy = ConstantHaircut(fraction=0.9)

    first = run(policy, stochastic(seed=11), SPRING_FORWARD, AFTER_SPRING_FORWARD,
                market=recorded(market_store)).scorecards["stochastic"]
    again = run(policy, stochastic(seed=11), SPRING_FORWARD, AFTER_SPRING_FORWARD,
                market=recorded(market_store)).scorecards["stochastic"]

    assert first == again
    assert first.seed == 11  # the scenario's seed, when the run doesn't pass one
