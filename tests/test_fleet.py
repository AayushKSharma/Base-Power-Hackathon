import pandas as pd
import pytest

from conftest import (
    AFTER_SPRING_FORWARD,
    CALM,
    MISSING_15MIN,
    NO_FAILURES,
    SPRING_FORWARD,
    STORM,
    FixedPolicy,
    Recorder,
    fleet_scenario,
    recorded,
    settlement_prices,
)
from harness import ConstantHaircut, run

HALF = {"P10": 0.5, "P25": 0.5, "P50": 0.5, "P75": 0.5, "P90": 0.5}


def at(policy, cpt):
    """The observation the policy got for the interval starting at `cpt` (local)."""
    return next(o for o in policy.seen if o["now"]["interval_start_cpt"].startswith(cpt))


def total(observation, field):
    return sum(r[field] for r in observation["fleet"]["regions"])


def rows_at(result, cpt, product="ECRS"):
    dump = result.intervals
    local = dump["interval_start_cpt"].dt.strftime("%Y-%m-%dT%H:%M")
    return dump[(local == cpt) & (dump["product"] == product)]


def test_over_and_under_sold_mw_h_are_exact_against_deliverable_mw(market_store):
    sc = fleet_scenario(shares=HALF)

    cards = run(FixedPolicy(ecrs=0.5, nonspin=0.05), sc, SPRING_FORWARD, SPRING_FORWARD,
                market=recorded(market_store)).scorecards

    # 50 of 100 homes available: D is 50 x 8 kW = 0.4 MW of ECRS and 50 x 2 kW =
    # 0.1 MW of Non-Spin, for the 23 hours of the spring-forward day.
    ecrs, nonspin = cards["P50"].totals["ECRS"], cards["P50"].totals["NONSPIN"]
    assert ecrs.deliverable_mw_h == pytest.approx(0.4 * 23)
    assert (ecrs.oversold_mw_h, ecrs.undersold_mw_h) == (pytest.approx(0.1 * 23), 0)
    assert (nonspin.oversold_mw_h, nonspin.undersold_mw_h) == (0, pytest.approx(0.05 * 23))


def test_overstatement_rate_and_oracle_regret_come_from_sums_and_counts(market_store):
    sc = fleet_scenario(shares=HALF)

    card = run(FixedPolicy(ecrs=0.5, nonspin=0.05), sc, SPRING_FORWARD, AFTER_SPRING_FORWARD,
               market=settlement_prices(market_store, ecrs=12.0, nonspin=3.0)).scorecards["P50"]

    ecrs, nonspin = card.totals["ECRS"], card.totals["NONSPIN"]
    assert (ecrs.overstated, ecrs.overstatement_rate) == (276 + 288, 1.0)
    assert (nonspin.overstated, nonspin.overstatement_rate) == (0, 0.0)
    # Regret against the oracle, which reports D: ECRS was over-sold, so nothing
    # was given up; Non-Spin left 0.1 - 0.05 MW unsold for 47 hours at $3.
    assert ecrs.revenue_given_up == 0
    assert nonspin.revenue_given_up == pytest.approx(0.05 * 3 * 47)
    assert card.to_dict()["totals"]["ECRS"]["overstatement_rate"] == 1.0


def test_exposure_counts_every_interval_and_dollars_only_priced_ones(market_store):
    card = run(FixedPolicy(ecrs=0.5, nonspin=0.05), fleet_scenario(shares=HALF), MISSING_15MIN, MISSING_15MIN,
               market=recorded(market_store)).scorecards["P50"]

    ecrs = card.totals["ECRS"]
    assert (ecrs.intervals, ecrs.skipped, ecrs.priced) == (288, 288, 0)
    assert ecrs.oversold_mw_h == pytest.approx(0.1 * 24)
    assert (ecrs.overstated, ecrs.revenue, ecrs.revenue_given_up) == (288, 0, 0)


def test_a_full_haircut_never_overstates_a_fleet_that_nothing_happens_to(market_store):
    # Beta SOC: some homes are held back by their 10 kW inverter, others by energy.
    sc = fleet_scenario(soc={"beta": [6, 4]})

    cards = run(ConstantHaircut(fraction=1.0), sc, SPRING_FORWARD, SPRING_FORWARD,
                market=recorded(market_store)).scorecards

    for card in cards.values():
        for totals in card.totals.values():
            assert (totals.overstated, totals.undersold_mw_h) == (0, pytest.approx(0))


def test_quantile_mode_scores_every_quantile_on_the_same_market_data(market_store):
    shares = {"P10": 0.2, "P25": 0.4, "P50": 0.5, "P75": 0.8, "P90": 1.0}

    cards = run(ConstantHaircut(fraction=0.9), fleet_scenario(shares=shares), SPRING_FORWARD,
                AFTER_SPRING_FORWARD, market=recorded(market_store)).scorecards

    assert list(cards) == ["P10", "P25", "P50", "P75", "P90"]
    assert {c.totals["ECRS"].priced for c in cards.values()} == {276 + 288}
    # 100 homes x 8 kW of ECRS x the share available, for 47 hours.
    assert [c.totals["ECRS"].deliverable_mw_h for c in cards.values()] == pytest.approx(
        [0.8 * s * 47 for s in shares.values()])
    # The haircut reports 90% of what it sees, so revenue scales with the share:
    # the same prices were used for every quantile.
    revenue = [c.totals["ECRS"].revenue for c in cards.values()]
    assert [r / revenue[-1] for r in revenue] == pytest.approx(list(shares.values()))


def test_a_quantile_mock_table_varies_by_cpt_month_and_hour(market_store, tmp_path):
    table = pd.DataFrame([(m, h) for m in range(1, 13) for h in range(24)], columns=["month", "hour"])
    share = ((table["month"] == 3) * (0.5 + 0.5 * (table["hour"] >= 12))).astype(float)
    for q in HALF:
        table[q] = share
    table.to_csv(tmp_path / "mock.csv", index=False)
    policy = Recorder()

    run(policy, fleet_scenario(shares=str(tmp_path / "mock.csv")), SPRING_FORWARD, SPRING_FORWARD,
        market=recorded(market_store))

    assert total(at(policy, "2026-03-08T11:55"), "homes_online") == 50
    assert total(at(policy, "2026-03-08T12:00"), "homes_online") == 100


def test_changing_ecrs_from_1_to_2_hours_halves_energy_limited_deliverable_mw(market_store):
    def deliverable(ecrs_h):
        sc = fleet_scenario(ecrs_h=ecrs_h)
        card = run(FixedPolicy(0, 0), sc, SPRING_FORWARD, SPRING_FORWARD,
                   market=recorded(market_store)).scorecards["P50"]
        return card.totals["ECRS"].deliverable_mw_h

    # 8 kWh above the floor: 8 kW for 1 hour, or 4 kW for 2 hours (inverter: 10 kW).
    assert deliverable(1) == pytest.approx(0.8 * 23)
    assert deliverable(2) == pytest.approx(0.4 * 23)


def test_a_forced_regional_outage_removes_exactly_that_regions_deliverable_mw(market_store):
    outage = {"region": 1, "start": "2026-03-08 14:00", "minutes": 120}
    sc = fleet_scenario(state="stochastic", failures={**NO_FAILURES, "forced_region_outages": [outage]})

    result = run(FixedPolicy(0, 0), sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    # Region 1 holds 25 of the 100 homes: 25 x 8 kW = 0.2 MW of ECRS.
    assert rows_at(result, "2026-03-08T12:55")["deliverable_mw"].item() == pytest.approx(0.8)
    assert rows_at(result, "2026-03-08T13:05")["deliverable_mw"].item() == pytest.approx(0.6)
    assert rows_at(result, "2026-03-08T15:55")["deliverable_mw"].item() == pytest.approx(0.6)
    assert rows_at(result, "2026-03-08T16:00")["deliverable_mw"].item() == pytest.approx(0.8)
    # ECRS windows (1 h) overlap the outage from 13:05 to 15:55; Non-Spin (4 h) from 10:05.
    card = result.scorecards["stochastic"]
    assert card.totals["ECRS"].deliverable_mw_h == pytest.approx(0.8 * 23 - 0.2 * 35 / 12)
    assert card.totals["NONSPIN"].deliverable_mw_h == pytest.approx(0.2 * 23 - 0.05 * 71 / 12)


def test_homes_in_backup_mode_are_observed_but_excluded_from_available_capability(market_store):
    outage = {"region": 0, "start": "2026-03-08 14:00", "minutes": 120}
    sc = fleet_scenario(state="stochastic", failures={**NO_FAILURES, "forced_region_outages": [outage]})
    policy = Recorder()

    run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    region0 = at(policy, "2026-03-08T14:00")["fleet"]["regions"][0]
    assert (region0["homes"], region0["homes_online"], region0["homes_backup"]) == (25, 0, 25)
    assert (region0["energy_above_floor_kwh"], region0["inverter_kw"]) == (0, 0)
    before = at(policy, "2026-03-08T13:55")["fleet"]["regions"][0]
    assert (before["homes_online"], before["energy_above_floor_kwh"]) == (25, pytest.approx(25 * 8))


def test_stale_homes_are_excluded_and_fresh_dropouts_look_online(market_store):
    # Every home drops out in the first five minutes, for an hour.
    sc = fleet_scenario(state="stochastic", failures={**NO_FAILURES, "home_dropout_per_h": 1.0})
    policy = Recorder()

    result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    # At 00:05 a dropout is under 5 minutes old: telemetry newer than 180 s still
    # looks online, though none of these homes can deliver.
    first = at(policy, "2026-03-08T00:05")
    assert 0 < total(first, "homes_stale") < 100
    assert total(first, "homes_online") == 100 - total(first, "homes_stale")
    assert rows_at(result, "2026-03-08T00:05")["deliverable_mw"].item() == 0
    # At 00:10 every dropout is over 5 minutes old, so every home is stale.
    later = at(policy, "2026-03-08T00:10")
    assert (total(later, "homes_stale"), total(later, "homes_online")) == (100, 0)
    assert total(later, "energy_above_floor_kwh") == 0


def test_every_policy_faces_the_same_failure_draws(market_store):
    sc = fleet_scenario(state="stochastic", failures=CALM, soc={"beta": [6, 4]}, homes=200, regions=10)

    def card(fraction, seed=3):
        return run(ConstantHaircut(fraction), sc, SPRING_FORWARD, AFTER_SPRING_FORWARD, seed=seed,
                   market=recorded(market_store)).scorecards["stochastic"]

    cautious, bold = card(0.5), card(0.9)

    for product in ("ECRS", "NONSPIN"):
        assert cautious.totals[product].deliverable_mw_h == bold.totals[product].deliverable_mw_h
    assert card(0.9) == bold
    assert card(0.9, seed=4).totals["ECRS"].deliverable_mw_h != bold.totals["ECRS"].deliverable_mw_h


def test_a_storm_raises_the_overstatement_rate_of_the_same_haircut(market_store):
    def overstatement(failures):
        sc = fleet_scenario(state="stochastic", failures=failures, soc={"beta": [6, 4]},
                            homes=200, regions=10)
        card = run(ConstantHaircut(0.9), sc, SPRING_FORWARD, AFTER_SPRING_FORWARD,
                   market=recorded(market_store)).scorecards["stochastic"]
        return {p: t.overstatement_rate for p, t in card.totals.items()}

    calm, storm = overstatement(CALM), overstatement(STORM)

    assert storm["ECRS"] > calm["ECRS"]
    assert storm["NONSPIN"] > calm["NONSPIN"]
