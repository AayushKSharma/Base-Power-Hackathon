import json

import pytest

from conftest import AFTER_SPRING_FORWARD, MISSING_15MIN, SPRING_FORWARD
from harness import ConstantHaircut, Scorecard, parse_scenario, run
from harness.market import load_intervals


def scenario(nominal_mw=80.0, cap_mw=100.0, cap_share=0.9, seed=7):
    return parse_scenario({
        "seed": seed,
        "fleet": {"nominal_mw": nominal_mw},
        "products": {
            "ECRS": {"cap_mw": cap_mw, "cap_share": cap_share},
            "NONSPIN": {"cap_mw": cap_mw, "cap_share": cap_share},
        },
    }, name="test")


def recorded(store):
    """The fixture market as recorded from ERCOT."""
    return lambda start, end: load_intervals(start, end, store_dir=store)


def settlement_prices(store, ecrs, nonspin):
    """The fixture market, with every 15-minute settlement MCPC replaced by a constant."""

    def load(start, end):
        df = load_intervals(start, end, store_dir=store)
        return df.assign(rt_mcpc_15m_ecrs=ecrs, rt_mcpc_15m_nspin=nonspin)

    return load


def test_reserve_revenue_is_award_times_settlement_price_times_interval_length(market_store):
    sc = scenario(nominal_mw=80)
    policy = ConstantHaircut(fraction=0.5, nominal_mw=80)

    card = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1,
               market=settlement_prices(market_store, ecrs=12.0, nonspin=3.0)).scorecard

    # 40 MW awarded for 23 hours: ECRS at $12/MW-h, Non-Spin at $3/MW-h.
    assert card.totals["ECRS"].intervals == 276
    assert card.totals["ECRS"].award_mw_h == pytest.approx(920.0)
    assert card.totals["ECRS"].revenue == pytest.approx(11_040.0)
    assert card.totals["NONSPIN"].revenue == pytest.approx(2_760.0)


def test_awards_are_limited_to_the_cap_share(market_store):
    sc = scenario(nominal_mw=200, cap_mw=100, cap_share=0.9)
    policy = ConstantHaircut(fraction=0.9, nominal_mw=200)

    card = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1,
               market=settlement_prices(market_store, ecrs=12.0, nonspin=3.0)).scorecard

    # Reports 180 MW, but one QSE may hold at most 90% of the 100 MW cap.
    ecrs = card.totals["ECRS"]
    assert ecrs.reported_mw_h == pytest.approx(180 * 23)
    assert ecrs.award_mw_h == pytest.approx(90 * 23)
    assert ecrs.revenue == pytest.approx(90 * 12.0 * 23)


def test_intervals_without_a_settlement_price_are_skipped_and_counted(market_store):
    policy = ConstantHaircut(fraction=0.9, nominal_mw=80)

    card = run(policy, scenario(), MISSING_15MIN, MISSING_15MIN, seed=1,
               market=recorded(market_store)).scorecard

    ecrs = card.totals["ECRS"]
    assert (ecrs.intervals, ecrs.skipped) == (288, 288)
    assert (ecrs.award_mw_h, ecrs.revenue) == (0.0, 0.0)


class Recorder:
    """Reports nothing and keeps every observation it is given."""

    name = "recorder"

    def __init__(self):
        self.seen = []

    def decide(self, observation):
        self.seen.append(observation)
        return {"ECRS": 0.0, "NONSPIN": 0.0}


def test_the_policy_observes_the_current_market_row_and_product_rules(market_store):
    policy = Recorder()

    run(policy, scenario(cap_mw=100, cap_share=0.9), SPRING_FORWARD, SPRING_FORWARD, seed=1,
        market=recorded(market_store))

    assert len(policy.seen) == 276
    obs = next(o for o in policy.seen if o["now"]["interval_start_utc"] == "2026-03-08T08:00:00+00:00")
    assert set(obs) == {"now", "history", "forecasts", "forecaster", "fleet", "products"}
    # NP6-332-CD SCED run 03:00:22 CDT; NP6-905-CD HE04 interval 1.
    assert obs["now"]["interval_start_cpt"] == "2026-03-08T03:00:00-05:00"
    assert obs["now"]["rt_mcpc"] == {"ECRS": 0.75, "NONSPIN": 3.0}
    assert obs["now"]["lz_price"] == {"HOUSTON": 37.24, "NORTH": 42.59, "SOUTH": 34.23, "WEST": 65.62}
    assert set(obs["now"]["scarce"]) == {"ECRS", "NONSPIN"}
    assert obs["products"] == {"ECRS": {"cap_mw": 100.0, "cap_share": 0.9},
                               "NONSPIN": {"cap_mw": 100.0, "cap_share": 0.9}}
    # Filled by later slices; present now so the interface doesn't change.
    assert obs["history"] == obs["forecasts"] == obs["forecaster"] == obs["fleet"] == {}
    # External policies receive it as JSON.
    assert json.loads(json.dumps(obs)) == obs


def test_a_date_range_run_combines_independent_day_runs(market_store):
    policy = ConstantHaircut(fraction=0.9, nominal_mw=80)
    days = (SPRING_FORWARD, AFTER_SPRING_FORWARD)

    whole = run(policy, scenario(), *days, seed=3, market=recorded(market_store)).scorecard
    single = [run(policy, scenario(), d, d, seed=3, market=recorded(market_store)).scorecard for d in days]

    assert whole == Scorecard.combine(single)
    assert [d.day for d in whole.days] == list(days)
    assert whole.totals["ECRS"].intervals == 276 + 288
    assert whole.totals["ECRS"].revenue == pytest.approx(
        single[0].totals["ECRS"].revenue + single[1].totals["ECRS"].revenue)


def test_the_same_seed_gives_an_identical_scorecard(market_store):
    policy = ConstantHaircut(fraction=0.9, nominal_mw=80)
    sc = scenario(seed=11)

    first = run(policy, sc, SPRING_FORWARD, AFTER_SPRING_FORWARD, market=recorded(market_store)).scorecard
    again = run(policy, sc, SPRING_FORWARD, AFTER_SPRING_FORWARD, market=recorded(market_store)).scorecard

    assert first == again
    assert first.seed == 11  # the scenario's seed, when the run doesn't pass one
