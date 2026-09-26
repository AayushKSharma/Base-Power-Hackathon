import json

import pandas as pd
import pytest

from conftest import SCENARIOS
from harness.cli import main

BASELINE = SCENARIOS / "baseline.yaml"


def harness_run(store, out, *args, scenario=BASELINE):
    return main(["run", "--scenario", str(scenario), "--market-dir", str(store),
                 "--out", str(out), *args])


def test_harness_run_prints_scorecards_and_writes_json_and_a_data_dump(market_store, tmp_path, capsys):
    out = tmp_path / "run"

    code = harness_run(market_store, out, "--policy", "constant_haircut", "--param", "fraction=0.8",
                       "--start", "2026-03-08", "--end", "2026-03-09", "--seed", "3")

    assert code == 0
    cards = json.loads((out / "scorecard.json").read_text())["scorecards"]
    assert [c["fleet_case"] for c in cards] == ["P10", "P25", "P50", "P75", "P90"]
    for card in cards:
        assert (card["policy"], card["scenario"], card["seed"]) == ("constant_haircut(fraction=0.8)", "baseline", 3)
        assert (card["policy_view"], card["observed_case"]) == ("typical", "P50")
        assert (card["start"], card["end"]) == ("2026-03-08", "2026-03-09")
        assert [d["day"] for d in card["days"]] == ["2026-03-08", "2026-03-09"]
        assert card["totals"]["ECRS"]["intervals"] == 276 + 288

    printed = capsys.readouterr().out
    assert "constant_haircut(fraction=0.8)" in printed
    assert f"{cards[0]['totals']['ECRS']['revenue']:,.2f}" in printed

    # One row per interval, fleet case and product; its totals reconcile to the scorecards.
    dump = pd.read_parquet(out / "intervals.parquet")
    assert len(dump) == (276 + 288) * 5 * 2
    assert set(dump["policy_view"]) == {"typical"}
    assert set(dump["observed_case"]) == {"P50"}
    sums = dump.groupby(["fleet_case", "product"])[
        ["revenue", "revenue_given_up", "oversold_mw", "deliverable_mw"]].sum()
    for card in cards:
        for product, totals in card["totals"].items():
            row = sums.loc[(card["fleet_case"], product)]
            assert row["revenue"] == pytest.approx(totals["revenue"])
            assert row["revenue_given_up"] == pytest.approx(totals["revenue_given_up"])
            assert row["oversold_mw"] * 5 / 60 == pytest.approx(totals["oversold_mw_h"])
            assert row["deliverable_mw"] * 5 / 60 == pytest.approx(totals["deliverable_mw_h"])


def test_harness_run_defaults_to_one_day_and_the_scenarios_seed(market_store, tmp_path):
    out = tmp_path / "run"

    assert harness_run(market_store, out, "--start", "2026-03-08") == 0

    card = json.loads((out / "scorecard.json").read_text())["scorecards"][0]
    assert (card["start"], card["end"], card["seed"]) == ("2026-03-08", "2026-03-08", 7)
    assert card["policy"] == "constant_haircut(fraction=0.9)"


def test_a_stochastic_scenario_gives_one_scorecard(market_store, tmp_path):
    out = tmp_path / "run"

    assert harness_run(market_store, out, "--start", "2026-03-08", scenario=SCENARIOS / "stochastic.yaml") == 0

    cards = json.loads((out / "scorecard.json").read_text())["scorecards"]
    assert [c["fleet_case"] for c in cards] == ["stochastic"]
    assert (cards[0]["policy_view"], cards[0]["observed_case"]) == ("per_case", "stochastic")


def test_an_invalid_scenario_is_reported_without_a_traceback(market_store, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(BASELINE.read_text().replace("battery_kwh", "battery_kw"))

    code = main(["run", "--scenario", str(bad), "--start", "2026-03-08",
                 "--market-dir", str(market_store), "--out", str(tmp_path / "run")])

    assert code == 2
    assert "fleet: unknown field 'battery_kw'" in capsys.readouterr().err


def test_an_unknown_policy_parameter_is_reported(market_store, tmp_path, capsys):
    code = harness_run(market_store, tmp_path / "run", "--start", "2026-03-08", "--param", "haircut=0.8")

    assert code == 2
    assert "haircut" in capsys.readouterr().err


def test_a_range_outside_the_built_dataset_says_how_to_build_it(market_store, tmp_path, capsys):
    code = harness_run(market_store, tmp_path / "run", "--start", "2026-03-08", "--end", "2026-03-10")

    assert code == 1
    assert "python -m harness.market build" in capsys.readouterr().err
