"""External policies speak JSON lines and are scored through harness.run."""

import json
import sys
from pathlib import Path

from conftest import AFTER_SPRING_FORWARD, SPRING_FORWARD, fleet_scenario
from harness import ConstantHaircut, ExternalPolicy, run
from harness.market import load_intervals

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "constant_haircut_policy.py"
SLOW = ROOT / "tests" / "policies" / "slow_policy.py"
CRASH = ROOT / "tests" / "policies" / "crash_policy.py"
GARBAGE = ROOT / "tests" / "policies" / "garbage_policy.py"
RECORDER = ROOT / "tests" / "policies" / "recorder_policy.py"
HELLO = ROOT / "tests" / "policies" / "hello_policy.py"

HOMES = [{"home_id": "h1", "region_id": "houston", "soc": 0.5, "last_seen_s": 12, "backup_mode": False}]


def scenario(**kwargs):
    return fleet_scenario(**kwargs)


def recorded(store):
    return lambda start, end: load_intervals(start, end, store_dir=store)


def _card(result):
    """The fleet case the policy actually saw. In the typical view the other cards copy its faults."""
    return next(card for card in result.scorecards.values() if card.fleet_case == card.observed_case)


def _reported(result, product):
    rows = result.intervals
    seen = rows["observed_case"].iloc[0]
    rows = rows.loc[(rows["product"] == product) & (rows["fleet_case"] == seen)]
    return rows.sort_values("interval_start_utc")["reported_mw"].tolist()


def _starts(rows):
    return [ts.isoformat() for ts in rows.index]


def test_constant_haircut_matches_in_process_through_the_external_protocol(market_store):
    sc = scenario()
    command = [sys.executable, str(EXAMPLE), "--fraction", "0.5"]

    with ExternalPolicy(command, sc.products) as policy:
        external = run(policy, sc, SPRING_FORWARD, AFTER_SPRING_FORWARD, seed=3,
                       market=recorded(market_store)).scorecards
    internal = run(ConstantHaircut(fraction=0.5), sc, SPRING_FORWARD, AFTER_SPRING_FORWARD,
                   seed=3, market=recorded(market_store)).scorecards

    assert external == internal


def test_a_slow_policy_times_out_and_the_fallback_counts_are_exact(market_store):
    sc = scenario()
    rows = load_intervals(SPRING_FORWARD, SPRING_FORWARD, store_dir=market_store)
    starts = _starts(rows)
    # First interval of the day, then one later interval that already has a good decision.
    slow = (starts[0], starts[2])
    command = [sys.executable, str(SLOW), "--sleep", "5", "--mw", "4"]
    for start in slow:
        command += ["--at", start]

    with ExternalPolicy(command, sc.products, timeout_s=0.2) as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    faults = _card(result).faults
    assert (faults.timeouts, faults.fallbacks, faults.malformed, faults.restarts) == (2, 2, 0, 0)
    reported = _reported(result, "ECRS")
    # No decision yet, so last-good falls back to zero. The next timeout reuses 4 MW.
    assert (reported[0], reported[1], reported[2], reported[3]) == (0.0, 4.0, 4.0, 4.0)


def test_a_timeout_with_zero_fallback_reports_zero(market_store):
    sc = scenario()
    rows = load_intervals(SPRING_FORWARD, SPRING_FORWARD, store_dir=market_store)
    slow = _starts(rows)[2]
    command = [sys.executable, str(SLOW), "--sleep", "5", "--mw", "4", "--at", slow]

    with ExternalPolicy(command, sc.products, timeout_s=0.2, fallback="zero") as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    reported = _reported(result, "ECRS")
    assert _card(result).faults.fallbacks == 1
    assert (reported[1], reported[2], reported[3]) == (4.0, 0.0, 4.0)


def test_a_policy_that_crashes_on_one_tick_is_restarted_and_the_run_completes(market_store):
    sc = scenario()
    rows = load_intervals(SPRING_FORWARD, SPRING_FORWARD, store_dir=market_store)
    crash_at = _starts(rows)[2]
    command = [sys.executable, str(CRASH), "--crash-at", crash_at, "--mw", "6"]

    with ExternalPolicy(command, sc.products, timeout_s=2) as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    faults = _card(result).faults
    assert (faults.restarts, faults.fallbacks, faults.timeouts, faults.malformed) == (1, 1, 0, 0)
    reported = _reported(result, "ECRS")
    assert len(reported) == 276
    # Ticks 1 and 2 report 6 MW. Tick 3 dies, so the harness repeats the last good report.
    assert reported[:4] == [6.0, 6.0, 6.0, 6.0]


def test_garbage_replies_are_counted_as_malformed_and_the_run_completes(market_store):
    sc = scenario()
    command = [sys.executable, str(GARBAGE), "--mw", "8",
               "--bad", "2=negative", "--bad", "3=non_numeric", "--bad", "4=missing"]

    with ExternalPolicy(command, sc.products) as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    faults = _card(result).faults
    assert (faults.malformed, faults.fallbacks, faults.timeouts, faults.restarts) == (3, 3, 0, 0)
    reported = _reported(result, "ECRS")
    assert len(reported) == 276
    # The three bad replies reuse the last good 8 MW, and the policy stays up.
    assert reported[:5] == [8.0, 8.0, 8.0, 8.0, 8.0]


def test_a_crash_during_hello_is_a_restart_and_the_run_completes(market_store, tmp_path):
    sc = scenario()
    command = [sys.executable, str(HELLO), "--state", str(tmp_path / "starts"),
               "--crash-hellos", "1", "--mw", "5"]

    with ExternalPolicy(command, sc.products, timeout_s=2) as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    faults = _card(result).faults
    assert (faults.restarts, faults.fallbacks, faults.malformed, faults.timeouts) == (1, 1, 0, 0)
    reported = _reported(result, "ECRS")
    assert (reported[0], reported[1]) == (0.0, 5.0)


def test_a_malformed_hello_is_counted_and_the_run_completes(market_store, tmp_path):
    sc = scenario()
    command = [sys.executable, str(HELLO), "--state", str(tmp_path / "starts"),
               "--bad-hellos", "1", "--mw", "5"]

    with ExternalPolicy(command, sc.products, timeout_s=2) as policy:
        result = run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store))

    faults = _card(result).faults
    assert (faults.malformed, faults.fallbacks, faults.restarts, faults.timeouts) == (1, 1, 0, 0)
    reported = _reported(result, "ECRS")
    assert len(reported) == 276
    assert (reported[0], reported[1]) == (0.0, 5.0)


def test_per_home_state_is_sent_only_when_the_policy_asks_for_it(market_store, tmp_path):
    sc = scenario()
    asked = tmp_path / "asked.jsonl"
    declined = tmp_path / "declined.jsonl"

    with ExternalPolicy([sys.executable, str(RECORDER), "--out", str(asked), "--wants-per-home"],
                        sc.products) as policy:
        run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store), homes=HOMES)
    with ExternalPolicy([sys.executable, str(RECORDER), "--out", str(declined)], sc.products) as policy:
        run(policy, sc, SPRING_FORWARD, SPRING_FORWARD, seed=1, market=recorded(market_store), homes=HOMES)

    seen = [json.loads(line) for line in asked.read_text().splitlines()]
    hidden = [json.loads(line) for line in declined.read_text().splitlines()]
    assert len(seen) == len(hidden) == 276
    assert seen[0]["homes"] == HOMES
    assert all("homes" not in obs and "homes" not in obs["fleet"] for obs in hidden)
    assert set(hidden[0]) == {"now", "history", "forecasts", "forecaster", "fleet", "products"}
