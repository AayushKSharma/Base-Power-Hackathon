"""Deployments, shortfall cost, and the reliability scorecard."""

import numpy as np
import pytest

from conftest import (
    AFTER_SPRING_FORWARD,
    CALM,
    NO_FAILURES,
    SCENARIOS,
    SPRING_FORWARD,
    STORM,
    FixedPolicy,
    fleet_scenario,
    recorded,
    settlement_prices,
)
from harness import ConstantHaircut, Scorecard, load_scenario, run
from harness.report import render_report

# One QSE holding 0.8 MW while region 0 (25 of 100 homes) is dark: true D is 0.6 MW.
# The gap is 0.2 MW. It lasts one 5-minute interval, so 0.2 * 5/60 = 1/60 MW-h.
SHORTFALL_MW = 0.2
SHORTFALL_MW_H = 1 / 60


def _with_prices(loader):
    def load(start, end):
        return loader(start, end).assign(
            rt_mcpc_15m_ecrs=12.0, rt_mcpc_15m_nspin=3.0,
            lz_spp_houston=250.0, lz_spp_north=250.0, lz_spp_south=250.0, lz_spp_west=250.0,
        )

    return load


def _prices(store):
    return _with_prices(settlement_prices(store, ecrs=12.0, nonspin=3.0))


def _forced_dark(preset):
    return fleet_scenario(
        state="stochastic",
        failures={**NO_FAILURES, "forced_region_outages": [
            {"region": 0, "start": "2026-03-08 14:00", "minutes": 120},
        ]},
        deployments={
            "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
            "forced": [{"product": "ECRS", "start": "2026-03-08 15:00", "minutes": 5}],
        },
        scoring={
            "preset": preset, "load_zone": "HOUSTON", "compliance_per_mw": 500.0, "spd_per_mwh": 100.0,
            "exceedance_mw": [0.0, 0.2, 1.0], "tolerance_mw": [0.2, 1.0],
        },
    )


def test_a_dark_region_during_a_forced_deployment_has_exact_shortfall_cost(market_store):
    # 75 homes x 8 kW = 0.6 MW. The policy reports the full 0.8 MW, so the shortfall is 0.2 MW.
    # Houston energy is $250/MWh and the ECRS settlement price is $12/MW-h.
    # Compliance is $500 per MW short.
    # (a) 250 * 0.2 * 1 h + 500 * 0.2 = 150
    # (b) tolerance is the lesser of 3% of 0.8 MW and 3 MW, so 0.024 MW.
    #     Charge is on 0.176 MW: 150 + 100 * 0.176 * 1 h = 167.6
    # (c) 12 * 0.2 * (5/60) h + 500 * 0.2 = 100.2
    policy = FixedPolicy(0.8, 0.0)

    costs = {}
    shortfall = {}
    for preset in ("energy", "energy_spd", "imbalance"):
        card = run(policy, _forced_dark(preset), SPRING_FORWARD, SPRING_FORWARD,
                   market=_prices(market_store)).scorecards["stochastic"]
        shortfall[preset] = card.totals["ECRS"].shortfall_mw_h
        costs[preset] = card.totals["ECRS"].shortfall_cost

    assert shortfall == pytest.approx({"energy": SHORTFALL_MW_H, "energy_spd": SHORTFALL_MW_H,
                                       "imbalance": SHORTFALL_MW_H})
    assert costs == pytest.approx({"energy": 150.0, "energy_spd": 167.6, "imbalance": 100.2})
    assert card.totals["NONSPIN"].shortfall_mw_h == 0


def _ecrs_at(result, cpt):
    dump = result.intervals
    local = dump["interval_start_cpt"].dt.strftime("%Y-%m-%dT%H:%M")
    return dump[(local == cpt) & (dump["product"] == "ECRS")]


def test_a_deployment_drains_stored_energy_and_does_not_cross_the_backup_floor(market_store):
    # 4 homes, 8 kWh above the floor, 8 kW of ECRS each. One call at 8 kW for 5 minutes
    # removes 2/3 kWh from each home, leaving 22/3 kWh, which is 22/3 kW next interval.
    sc = fleet_scenario(
        state="stochastic", homes=4, regions=4,
        deployments={
            "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
            "forced": [{"product": "ECRS", "start": "2026-03-08 15:00", "minutes": 5}],
        },
    )

    result = run(FixedPolicy(0.032, 0.0), sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    assert _ecrs_at(result, "2026-03-08T15:00")["deliverable_mw"].item() == pytest.approx(0.032)
    assert _ecrs_at(result, "2026-03-08T15:05")["deliverable_mw"].item() == pytest.approx(88 / 3000)
    assert result.scorecards["stochastic"].backup_floor_violations == 0


def test_an_idle_interval_refills_at_the_configured_rate_and_stops_at_full(market_store):
    # SOC 0.99 leaves 15.8 kWh above the floor: 3.95 kW of Non-Spin on a 4-hour product.
    # 12 kW for 5 minutes would add 1 kWh, but only 0.2 kWh fits under a full battery,
    # so the next interval is exactly 4 kW per home.
    sc = fleet_scenario(
        state="stochastic", homes=4, regions=4, soc=0.99,
        deployments={"calm": 0.0, "scarce": 0.0, "refill_kw": 12.0, "forced": []},
    )

    result = run(FixedPolicy(0.0, 0.0), sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))
    dump = result.intervals
    local = dump["interval_start_cpt"].dt.strftime("%Y-%m-%dT%H:%M")

    def at(cpt):
        return dump[(local == cpt) & (dump["product"] == "NONSPIN")]["deliverable_mw"].item()

    assert at("2026-03-08T00:00") == pytest.approx(0.0158)
    assert at("2026-03-08T00:05") == pytest.approx(0.016)


def _first_hour_scarce(store):
    """The recorded day, with ECRS scarce only for the first twelve intervals."""

    def load(start, end):
        df = recorded(store)(start, end).copy()
        df["scarce_ecrs"] = False
        df.iloc[:12, df.columns.get_loc("scarce_ecrs")] = True
        df["scarce_nspin"] = False
        return df

    return load


def _deployed(result, product, case):
    rows = result.intervals
    rows = rows[(rows["product"] == product) & (rows["fleet_case"] == case)].sort_values("interval_start_utc")
    return rows["deployed"].to_numpy()


def test_deployment_draws_follow_scarcity_and_are_identical_across_policies(market_store):
    market = _first_hour_scarce(market_store)
    certain = fleet_scenario(deployments={"calm": 0.0, "scarce": 1.0, "refill_kw": 0.0, "forced": []})

    forced_run = run(FixedPolicy(0.1, 0.1), certain, SPRING_FORWARD, SPRING_FORWARD, seed=3, market=market)
    ecrs = _deployed(forced_run, "ECRS", "P50")
    nonspin = _deployed(run(ConstantHaircut(0.4), certain, SPRING_FORWARD, SPRING_FORWARD, seed=3,
                            market=market), "NONSPIN", "P10")

    # Scarce probability 1 and calm probability 0: ECRS deploys on exactly the twelve flagged
    # intervals, and Non-Spin, which is never scarce, does not deploy.
    assert ecrs.sum() == 12
    assert ecrs[:12].all() and not ecrs[12:].any()
    assert not nonspin.any()

    chance = fleet_scenario(state="stochastic", failures=NO_FAILURES, homes=40, regions=4,
                            deployments={"calm": 0.5, "scarce": 0.5, "refill_kw": 0.0, "forced": []})
    cautious = _deployed(run(ConstantHaircut(0.2), chance, SPRING_FORWARD, SPRING_FORWARD, seed=5,
                             market=market), "ECRS", "stochastic")
    bold = _deployed(run(ConstantHaircut(0.9), chance, SPRING_FORWARD, SPRING_FORWARD, seed=5,
                         market=market), "ECRS", "stochastic")
    other_seed = _deployed(run(ConstantHaircut(0.9), chance, SPRING_FORWARD, SPRING_FORWARD, seed=6,
                               market=market), "ECRS", "stochastic")
    same_calls = [_deployed(forced_run, "ECRS", case) for case in ("P10", "P90")]
    assert np.array_equal(same_calls[0], same_calls[1])
    assert np.array_equal(cautious, bold)
    assert not np.array_equal(bold, other_seed)


def test_calm_and_scarce_splits_sum_to_the_product_totals(market_store):
    # ECRS is scarce for the first hour only. The policy reports 1 MW of ECRS and
    # 0.2 MW of Non-Spin into a 0.8 / 0.2 MW fleet. One deployment at midnight,
    # inside that scarce hour: shortfall 0.2 MW for five minutes.
    # ECRS revenue: 1 MW * $12/MW-h. Scarce hour $12, the other 22 hours $264.
    # Shortfall cost: $250/MWh * 0.2 MW * 1 h = $50, all of it scarce.
    # Non-Spin is never scarce: 0.2 MW * $3/MW-h * 23 h = $13.80, all calm.
    sc = fleet_scenario(
        deployments={
            "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
            "forced": [{"product": "ECRS", "start": "2026-03-08 00:00", "minutes": 5}],
        },
        scoring={
            "preset": "energy", "load_zone": "HOUSTON", "compliance_per_mw": 0.0, "spd_per_mwh": 0.0,
            "exceedance_mw": [0.0, 1.0], "tolerance_mw": [1.0],
        },
    )
    card = run(FixedPolicy(1.0, 0.2), sc, SPRING_FORWARD, SPRING_FORWARD,
               market=_with_prices(_first_hour_scarce(market_store))).scorecards["P50"]

    ecrs_scarce, ecrs_calm = card.scarce["ECRS"], card.calm["ECRS"]
    assert (ecrs_scarce.intervals, ecrs_calm.intervals) == (12, 264)
    assert ecrs_scarce.revenue == pytest.approx(12)
    assert ecrs_calm.revenue == pytest.approx(264)
    assert ecrs_scarce.shortfall_mw_h == pytest.approx(1 / 60)
    assert ecrs_calm.shortfall_mw_h == 0
    assert ecrs_scarce.shortfall_cost == pytest.approx(50)
    assert ecrs_calm.shortfall_cost == 0
    assert card.scarce["NONSPIN"].intervals == 0
    assert card.calm["NONSPIN"].revenue == pytest.approx(13.8)

    for product in ("ECRS", "NONSPIN"):
        part = card.calm[product] + card.scarce[product]
        whole = card.totals[product]
        assert part.intervals == whole.intervals
        assert part.revenue == pytest.approx(whole.revenue)
        assert part.shortfall_mw_h == pytest.approx(whole.shortfall_mw_h)
        assert part.shortfall_cost == pytest.approx(whole.shortfall_cost)
        assert part.net == pytest.approx(whole.net)
        assert part.skipped == whole.skipped


def test_exceedance_and_the_tolerance_table_are_exact_on_each_fleet_case(market_store):
    # P10 has 50 homes (0.4 MW of ECRS); P50 has all 100 (0.8 MW). The policy
    # reports 0.6 MW. One deployment in the last five minutes of the day, so
    # no later interval has a drained battery.
    # 23 hours. One hour reaches 0.2 MW on P10 and none of them do on P50.
    # 0.2 MW is 20% of the 1 MW nameplate (100 homes x 10 kW).
    # Revenue is 0.6 MW * $12/MW-h * 23 h = $165.60 either way.
    # P50 left 0.2 MW unsold: 0.2 * $12 * 23 h = $55.20 given up. P10 gave up nothing.
    shares = {"P10": 0.5, "P25": 1.0, "P50": 1.0, "P75": 1.0, "P90": 1.0}
    sc = fleet_scenario(
        shares=shares,
        deployments={
            "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
            "forced": [{"product": "ECRS", "start": "2026-03-08 23:55", "minutes": 5}],
        },
    )
    cards = run(FixedPolicy(0.6, 0.2), sc, SPRING_FORWARD, SPRING_FORWARD,
                market=_prices(market_store)).scorecards

    low = cards["P10"].exceedance("ECRS", (0.0, 0.2, 1.0), fleet_mw=1.0)
    typical = cards["P50"].exceedance("ECRS", (0.0, 0.2, 1.0), fleet_mw=1.0)
    assert [(p.mw, p.fleet_pct) for p in low] == [(0.0, 0.0), (0.2, 20.0), (1.0, 100.0)]
    assert [p.probability for p in low] == pytest.approx([1.0, 1 / 23, 0.0])
    assert [p.probability for p in typical] == pytest.approx([1.0, 0.0, 0.0])

    low_tol = cards["P10"].tolerance("ECRS", (0.2, 1.0))
    typical_tol = cards["P50"].tolerance("ECRS", (0.2, 1.0))
    assert low_tol[0].probability == pytest.approx(1 / 23)
    assert low_tol[1].probability == 0
    assert (low_tol[0].revenue, low_tol[1].revenue) == pytest.approx((165.6, 165.6))
    assert low_tol[0].revenue_given_up == 0
    assert typical_tol[0].probability == 0
    assert typical_tol[0].revenue_given_up == pytest.approx(55.2)


def test_the_interval_dump_reconciles_to_the_scorecard(market_store):
    result = run(FixedPolicy(0.8, 0.0), _forced_dark("energy_spd"), SPRING_FORWARD, SPRING_FORWARD,
                 market=_prices(market_store))
    totals = result.scorecards["stochastic"].totals["ECRS"]
    rows = result.intervals
    rows = rows[(rows["fleet_case"] == "stochastic") & (rows["product"] == "ECRS")]

    assert rows["shortfall_mw"].sum() * 5 / 60 == pytest.approx(totals.shortfall_mw_h)
    assert rows["shortfall_cost"].sum() == pytest.approx(totals.shortfall_cost)
    assert rows["revenue"].sum() == pytest.approx(totals.revenue)
    assert rows["award_mw"].sum() * 5 / 60 == pytest.approx(totals.award_mw_h)
    assert rows["revenue"].sum() - rows["shortfall_cost"].sum() == pytest.approx(totals.net)


@pytest.mark.parametrize("seed, fraction", [(1, 0.0), (4, 0.5), (9, 1.0), (13, 1.4)])
def test_backup_floor_violations_are_always_zero(market_store, seed, fraction):
    sc = fleet_scenario(
        state="stochastic", failures=STORM, soc={"beta": [2, 2]}, homes=24, regions=6, seed=seed,
        deployments={"calm": 0.35, "scarce": 1.0, "refill_kw": 4.0, "forced": []},
    )

    card = run(ConstantHaircut(fraction), sc, SPRING_FORWARD, AFTER_SPRING_FORWARD, seed=seed,
               market=recorded(market_store)).scorecards["stochastic"]

    assert card.backup_floor_violations == 0


def test_a_date_range_with_deployments_equals_the_combination_of_its_days(market_store):
    sc = fleet_scenario(
        state="stochastic", failures=CALM, soc={"beta": [6, 4]}, homes=16, regions=4,
        deployments={"calm": 0.25, "scarce": 0.7, "refill_kw": 2.0, "forced": []},
    )
    days = (SPRING_FORWARD, AFTER_SPRING_FORWARD)

    whole = run(ConstantHaircut(0.8), sc, *days, seed=3, market=recorded(market_store)).scorecards
    single = [run(ConstantHaircut(0.8), sc, d, d, seed=3, market=recorded(market_store)).scorecards for d in days]

    assert whole["stochastic"] == Scorecard.combine(s["stochastic"] for s in single)


PRESETS = ("baseline", "storm_houston", "caps_lifted", "nonspin_2h", "ecrs_2h", "fleet_10x")


@pytest.mark.parametrize("name", PRESETS)
def test_every_named_preset_loads_and_runs(market_store, name):
    sc = load_scenario(SCENARIOS / f"{name}.yaml")

    result = run(ConstantHaircut(0.9), sc, SPRING_FORWARD, SPRING_FORWARD, market=recorded(market_store))

    assert sc.name == name
    assert result.scorecards


def test_the_other_presets_override_one_baseline_knob_each():
    two_hour = load_scenario(SCENARIOS / "nonspin_2h.yaml")
    ecrs = load_scenario(SCENARIOS / "ecrs_2h.yaml")
    fleet = load_scenario(SCENARIOS / "fleet_10x.yaml")
    storm = load_scenario(SCENARIOS / "storm_houston.yaml")

    assert two_hour.products["NONSPIN"].duration_h == 2
    assert two_hour.products["ECRS"].duration_h == 1
    assert ecrs.products["ECRS"].duration_h == 2
    assert ecrs.products["NONSPIN"].duration_h == 4
    assert (fleet.fleet.homes, fleet.fleet.regions) == (10000, 100)
    assert storm.fleet.state == "stochastic"
    assert storm.failures.home_dropout_per_h == pytest.approx(0.04)
    assert storm.failures.region_outage_per_h == pytest.approx(0.08)
    assert storm.scoring.load_zone == "HOUSTON"
    assert [o.region for o in storm.failures.forced_region_outages] == [0]


def test_caps_lifted_raises_the_award_limit_above_baseline(market_store):
    baseline = load_scenario(SCENARIOS / "baseline.yaml")
    lifted = load_scenario(SCENARIOS / "caps_lifted.yaml")
    assert baseline.products["ECRS"].award_limit_mw == pytest.approx(90)
    assert lifted.products["ECRS"].award_limit_mw == pytest.approx(450)
    assert lifted.products["NONSPIN"].duration_h == 4  # only the cap changes

    def awarded(sc):
        card = run(FixedPolicy(200, 200), sc, SPRING_FORWARD, SPRING_FORWARD,
                   market=recorded(market_store)).scorecards["P50"]
        return card.totals["ECRS"].award_mw_h

    assert awarded(baseline) == pytest.approx(90 * 23)
    assert awarded(lifted) == pytest.approx(200 * 23)


def test_the_report_charts_exceedance_per_fleet_case_and_labels_assumptions(market_store):
    shares = {"P10": 0.5, "P25": 1.0, "P50": 1.0, "P75": 1.0, "P90": 1.0}
    sc = fleet_scenario(
        shares=shares,
        deployments={
            "calm": 0.0, "scarce": 0.0, "refill_kw": 0.0,
            "forced": [{"product": "ECRS", "start": "2026-03-08 23:55", "minutes": 5}],
        },
        scoring={
            "preset": "energy", "load_zone": "HOUSTON", "compliance_per_mw": 500.0, "spd_per_mwh": 0.0,
            "exceedance_mw": [0.0, 0.2, 1.0], "tolerance_mw": [0.2, 1.0],
        },
    )
    result = run(FixedPolicy(0.6, 0.2), sc, SPRING_FORWARD, SPRING_FORWARD, market=_prices(market_store))

    text = render_report(result, sc)

    # 1 of 23 hours on P10 exceeds 0.2 MW, which is 20% of the 1 MW nameplate.
    assert "P10" in text and "P50" in text
    assert "4.3%" in text
    assert "20%" in text
    # $250/MWh * 0.2 MW * 1 h + $500/MW * 0.2 MW = $150 shortfall cost on P10.
    # Revenue $165.60, so net is $15.60.
    assert "165.60" in text
    assert "15.60" in text
    assert "load-zone RT price" in text and "real data" in text
    assert "compliance cost" in text and "assumption" in text
    assert result.scorecards["P10"].to_dict()["inputs"]["compliance cost"] == "assumption"
