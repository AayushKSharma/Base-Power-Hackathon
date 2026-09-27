"""`harness compare`: several policies, several scenarios, reliability first."""

import sys
import xml.etree.ElementTree as ET

import pandas as pd

from conftest import SCENARIOS, SPRING_FORWARD
from harness.cli import main

EXAMPLE = SCENARIOS.parents[0] / "examples" / "constant_haircut_policy.py"
DAY = SPRING_FORWARD.isoformat()


def test_compare_scores_policies_and_scenarios_together(market_store, tmp_path, capsys):
    # A built-in and an external command, on the baseline and a forward-looking preset.
    out = tmp_path / "compare"
    external = f"{sys.executable} {EXAMPLE} --fraction 0.5"

    code = main([
        "compare",
        "--policy", "constant_haircut",
        "--policy", external,
        "--scenario", "baseline",
        "--scenario", "caps_lifted",
        "--start", DAY,
        "--seed", "3",
        "--market-dir", str(market_store),
        "--out", str(out),
    ])

    assert code == 0
    printed = capsys.readouterr().out
    for label in ("Scenario", "Policy", "Shortfall MW-h", "Overstated", "Net $", "Regret $",
                  "Timeouts", "Malformed", "Restarts", "Fallbacks"):
        assert label in printed
    assert "constant_haircut(fraction=0.9)" in printed
    assert "constant_haircut(fraction=0.5)" in printed
    assert "baseline" in printed
    assert "caps_lifted" in printed

    report = (out / "comparison.md").read_text()
    assert "P(under-serve" in report
    assert "Revenue given up" in report
    assert "P10" in report and "P90" in report
    assert "baseline" in report and "caps_lifted" in report

    dump = pd.read_parquet(out / "intervals.parquet")
    assert set(dump["scenario"]) == {"baseline", "caps_lifted"}
    assert set(dump["policy"]) >= {"constant_haircut(fraction=0.9)", "constant_haircut(fraction=0.5)"}
    assert {"deployed", "deliverable_mw", "reported_mw", "award_mw"} <= set(dump.columns)


def test_every_policy_faces_the_same_failure_and_deployment_draws(market_store, tmp_path):
    # Stochastic mode draws home dropouts, regional outages and deployments.
    # A built-in and an external policy that report different MW must still see those draws.
    out = tmp_path / "compare"
    external = f"{sys.executable} {EXAMPLE} --fraction 0.5"

    code = main([
        "compare",
        "--policy", "constant_haircut",
        "--policy", external,
        "--scenario", "stochastic",
        "--start", DAY,
        "--seed", "3",
        "--market-dir", str(market_store),
        "--out", str(out),
    ])

    assert code == 0
    dump = pd.read_parquet(out / "intervals.parquet")
    policies = sorted(dump["policy"].unique())
    assert {"constant_haircut(fraction=0.5)", "constant_haircut(fraction=0.9)"} <= set(policies)
    keys = ["interval_start_utc", "fleet_case", "product"]
    frames = {
        policy: dump[dump["policy"] == policy].sort_values(keys).reset_index(drop=True)
        for policy in policies
    }
    base = next(iter(frames.values()))
    opening = base["interval_start_utc"] == base["interval_start_utc"].min()
    for frame in frames.values():
        assert frame["deployed"].tolist() == base["deployed"].tolist()
        assert frame.loc[opening, "deliverable_mw"].tolist() == base.loc[opening, "deliverable_mw"].tolist()
    # The named policies really did diverge, so matching draws are not a shared decision.
    assert (frames["constant_haircut(fraction=0.5)"]["reported_mw"].tolist()
            != frames["constant_haircut(fraction=0.9)"]["reported_mw"].tolist())


def test_the_frontier_draws_the_reliability_target_at_several_epsilons(market_store, tmp_path):
    out = tmp_path / "compare"

    code = main([
        "compare",
        "--policy", "constant_haircut",
        "--scenario", "baseline",
        "--start", DAY,
        "--seed", "3",
        "--market-dir", str(market_store),
        "--out", str(out),
    ])

    assert code == 0
    names = set()
    curved = False
    for el in ET.fromstring((out / "frontier.svg").read_text()).iter():
        if el.get("data-policy"):
            names.add(el.get("data-policy"))
        if el.get("data-curve") == "reliability_target":
            curved = True
    for epsilon in ("0.01", "0.05", "0.1", "0.2"):
        assert f"reliability_target(epsilon={epsilon})" in names
    assert curved


def test_compare_writes_reliability_charts_for_the_fixture(market_store, tmp_path):
    out = tmp_path / "compare"

    code = main([
        "compare",
        "--policy", "constant_haircut",
        "--scenario", "baseline",
        "--scenario", "caps_lifted",
        "--start", DAY,
        "--day", DAY,
        "--seed", "3",
        "--market-dir", str(market_store),
        "--out", str(out),
    ])

    assert code == 0
    for name in ("frontier.png", "exceedance.png", "capability.png", "rankings.png"):
        chart = (out / "charts" / name).read_bytes()
        assert chart.startswith(b"\x89PNG\r\n\x1a\n")
    report = (out / "comparison.md").read_text()
    assert ("Rankings flip between baseline and caps_lifted." in report
            or "Rankings hold between baseline and caps_lifted." in report)


def test_compare_reports_a_bad_date_range_and_a_bad_scenario(market_store, tmp_path, capsys):
    out = tmp_path / "compare"
    shared = ["compare", "--policy", "constant_haircut", "--market-dir", str(market_store),
              "--out", str(out)]

    code = main([*shared, "--scenario", "baseline", "--start", DAY, "--end", "2026-03-07"])

    assert code == 2
    assert "error" in capsys.readouterr().err

    code = main([*shared, "--scenario", "no-such-preset", "--start", DAY])

    assert code == 2
    err = capsys.readouterr().err
    assert "error" in err
    assert "no-such-preset" in err

    code = main([*shared, "--scenario", "baseline", "--start", DAY, "--day", "2026-03-01"])

    assert code == 2
    assert "--day" in capsys.readouterr().err


def test_compare_reports_missing_market_data(market_store, tmp_path, capsys):
    code = main([
        "compare", "--policy", "constant_haircut", "--scenario", "baseline",
        "--start", "2020-01-01", "--market-dir", str(market_store),
        "--out", str(tmp_path / "compare"),
    ])

    assert code == 1
    assert "missing" in capsys.readouterr().err
