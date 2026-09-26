import datetime as dt
import socket
from pathlib import Path

import pytest

from harness import parse_scenario
from harness.market import build_dataset, load_intervals

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

# Recorded days the harness tests run on (see scripts/record_fixtures.py).
SPRING_FORWARD = dt.date(2026, 3, 8)  # 23 hours: 276 five-minute intervals
AFTER_SPRING_FORWARD = dt.date(2026, 3, 9)
MISSING_15MIN = dt.date(2026, 3, 19)  # no NP6-331-CD settlement-price file


class NetworkDisabled(RuntimeError):
    pass


def _refuse_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise NetworkDisabled("network access attempted during a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline: the dataset is built from recorded raw files."""
    _refuse_network(monkeypatch)


@pytest.fixture(scope="session")
def market_store(tmp_path_factory):
    """A market dataset built from the recorded raw fixtures, for harness runs."""
    out = tmp_path_factory.mktemp("market")
    # Session fixtures set up before the per-test guard, so guard the build too.
    with pytest.MonkeyPatch.context() as monkeypatch:
        _refuse_network(monkeypatch)
        for day in (SPRING_FORWARD, AFTER_SPRING_FORWARD, MISSING_15MIN):
            build_dataset(day, day, raw_dir=FIXTURE_RAW, store_dir=out, fetch=False)
    return out


def recorded(store):
    """The fixture market as recorded from ERCOT, as a market loader for `run`."""
    return lambda start, end: load_intervals(start, end, store_dir=store)


def settlement_prices(store, ecrs, nonspin):
    """The fixture market, with every 15-minute settlement MCPC replaced by a constant."""

    def load(start, end):
        df = load_intervals(start, end, store_dir=store)
        return df.assign(rt_mcpc_15m_ecrs=ecrs, rt_mcpc_15m_nspin=nonspin)

    return load


FLAT_SHARES = {"P10": 1.0, "P25": 1.0, "P50": 1.0, "P75": 1.0, "P90": 1.0}
NO_FAILURES = {
    "home_dropout_per_h": 0.0, "home_dropout_min": 60, "region_outage_per_h": 0.0,
    "region_outage_min": 120, "scarcity_stress": 1.0, "forced_region_outages": [],
}
# Handoff section 8's calm and storm dropout chances.
CALM = {**NO_FAILURES, "home_dropout_per_h": 0.01, "region_outage_per_h": 0.005}
STORM = {**NO_FAILURES, "home_dropout_per_h": 0.04, "region_outage_per_h": 0.08}


def fleet_scenario(*, state="quantile", shares=FLAT_SHARES, view="typical", typical="P50",
                   failures=NO_FAILURES, homes=100, regions=4, soc=0.6, ecrs_h=1, nonspin_h=4,
                   cap_mw=100.0, cap_share=0.9, seed=7):
    """A small, hand-computable scenario.

    At the default 60% SOC each home has 8 kWh above its 20% floor and a 10 kW
    inverter, so it can deliver 8 kW of ECRS (1 h) and 2 kW of Non-Spin (4 h).
    """
    return parse_scenario({
        "seed": seed,
        "fleet": {
            "homes": homes, "regions": regions, "battery_kwh": 20, "inverter_kw": 10,
            "backup_floor": 0.2, "soc": {"fixed": soc} if isinstance(soc, float) else soc,
            "telemetry_stale_s": 180, "state": state,
            "quantile_mock": {"shares": shares, "policy_view": view, "typical": typical},
        },
        "failures": failures,
        "products": {
            "ECRS": {"duration_h": ecrs_h, "cap_mw": cap_mw, "cap_share": cap_share},
            "NONSPIN": {"duration_h": nonspin_h, "cap_mw": cap_mw, "cap_share": cap_share},
        },
    }, name="test")


class FixedPolicy:
    """Reports the same MW every interval, whatever it observes."""

    def __init__(self, ecrs, nonspin):
        self.capability = {"ECRS": ecrs, "NONSPIN": nonspin}
        self.name = f"fixed({ecrs}, {nonspin})"

    def decide(self, observation):
        return self.capability


class Recorder(FixedPolicy):
    """Reports nothing and keeps every observation it is given."""

    def __init__(self):
        super().__init__(0.0, 0.0)
        self.seen = []

    def decide(self, observation):
        self.seen.append(observation)
        return super().decide(observation)
