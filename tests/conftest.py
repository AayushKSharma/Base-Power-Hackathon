import socket
from pathlib import Path

import pytest

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"


class NetworkDisabled(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline: the dataset is built from recorded raw files."""

    def refuse(*args, **kwargs):
        raise NetworkDisabled("network access attempted during a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
