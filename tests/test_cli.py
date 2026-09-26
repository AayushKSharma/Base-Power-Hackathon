import json

import pandas as pd
import pytest

from conftest import MINIMAL_SCENARIO as MINIMAL
from harness.cli import main


def harness_run(store, out, *args):
    return main(["run", "--scenario", str(MINIMAL), "--market-dir", str(store),
                 "--out", str(out), *args])


def test_harness_run_prints_a_scorecard_and_writes_json_and_a_data_dump(market_store, tmp_path, capsys):
    out = tmp_path / "run"

    code = harness_run(market_store, out, "--policy", "constant_haircut", "--param", "fraction=0.8",
                       "--start", "2026-03-08", "--end", "2026-03-09", "--seed", "3")

    assert code == 0
    card = json.loads((out / "scorecard.json").read_text())
    assert (card["policy"], card["scenario"], card["seed"]) == ("constant_haircut(fraction=0.8)", "minimal", 3)
    assert (card["start"], card["end"]) == ("2026-03-08", "2026-03-09")
    assert [d["day"] for d in card["days"]] == ["2026-03-08", "2026-03-09"]
    ecrs = card["totals"]["ECRS"]
    assert ecrs["intervals"] == 276 + 288
    # 0.8 x 81 MW = 64.8 MW reported every interval, under the 90 MW award limit.
    assert ecrs["award_mw_h"] == pytest.approx(64.8 * 47)

    printed = capsys.readouterr().out
    assert "constant_haircut(fraction=0.8)" in printed
    assert f"{ecrs['revenue']:,.2f}" in printed

    dump = pd.read_parquet(out / "intervals.parquet")
    assert len(dump) == (276 + 288) * 2  # one row per interval and product
    revenue = dump.groupby("product")["revenue"].sum()
    assert revenue["ECRS"] == pytest.approx(ecrs["revenue"])
    assert revenue["NONSPIN"] == pytest.approx(card["totals"]["NONSPIN"]["revenue"])


def test_harness_run_defaults_to_one_day_and_the_scenarios_seed(market_store, tmp_path):
    out = tmp_path / "run"

    assert harness_run(market_store, out, "--start", "2026-03-08") == 0

    card = json.loads((out / "scorecard.json").read_text())
    assert (card["start"], card["end"], card["seed"]) == ("2026-03-08", "2026-03-08", 7)
    assert card["policy"] == "constant_haircut(fraction=0.9)"


def test_an_invalid_scenario_is_reported_without_a_traceback(market_store, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(MINIMAL.read_text().replace("nominal_mw", "nominal_kw"))

    code = main(["run", "--scenario", str(bad), "--start", "2026-03-08",
                 "--market-dir", str(market_store), "--out", str(tmp_path / "run")])

    assert code == 2
    assert "fleet: unknown field 'nominal_kw'" in capsys.readouterr().err


def test_an_unknown_policy_parameter_is_reported(market_store, tmp_path, capsys):
    code = harness_run(market_store, tmp_path / "run", "--start", "2026-03-08", "--param", "haircut=0.8")

    assert code == 2
    assert "haircut" in capsys.readouterr().err


def test_a_range_outside_the_built_dataset_says_how_to_build_it(market_store, tmp_path, capsys):
    code = harness_run(market_store, tmp_path / "run", "--start", "2026-03-08", "--end", "2026-03-10")

    assert code == 1
    assert "python -m harness.market build" in capsys.readouterr().err
