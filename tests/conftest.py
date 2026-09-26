import datetime as dt
import socket
from pathlib import Path

import pytest

from harness.market import build_dataset

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
MINIMAL_SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "minimal.yaml"

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
