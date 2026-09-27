"""Durable coordinator state: desired MW, allocations, command versions, leases.

A restarted coordinator loads this and treats every agent as unknown until
that agent heartbeats again. The versions already applied stay in the table,
so the new commands are newer and the hosts do not discharge twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass
class SavedCoordinator:
    """One committed snapshot of the control loop."""

    desired_mw: dict[str, float]
    interval: int | None
    allocations: dict[int, float]
    versions: dict[int, int]
    leases: dict[int, float]


class MemoryStore:
    """In-process snapshot. It dies with the coordinator, so recovery uses Postgres."""

    def __init__(self) -> None:
        self._saved = SavedCoordinator({}, None, {}, {}, {})

    def load(self) -> SavedCoordinator:
        saved = self._saved
        return SavedCoordinator(
            dict(saved.desired_mw), saved.interval, dict(saved.allocations),
            dict(saved.versions), dict(saved.leases),
        )

    def save(self, saved: SavedCoordinator) -> None:
        self._saved = saved


class PostgresStore:
    """The same snapshot in the harness Postgres."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def load(self) -> SavedCoordinator:
        self._ensure()
        with psycopg.connect(self.dsn) as conn:
            desired_rows = conn.execute("SELECT product, mw, interval FROM replay_desired").fetchall()
            allocation_rows = conn.execute(
                "SELECT agent_id, target_kw, version FROM replay_allocation").fetchall()
            lease_rows = conn.execute("SELECT agent_id, heartbeat_s FROM replay_lease").fetchall()
        if not desired_rows:
            return SavedCoordinator({}, None, {}, {}, {})
        return SavedCoordinator(
            {str(product): float(mw) for product, mw, _interval in desired_rows},
            int(desired_rows[0][2]),
            {int(agent): float(target) for agent, target, _version in allocation_rows},
            {int(agent): int(version) for agent, _target, version in allocation_rows},
            {int(agent): float(heartbeat) for agent, heartbeat in lease_rows},
        )

    def save(self, saved: SavedCoordinator) -> None:
        self._ensure()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM replay_desired")
            conn.execute("DELETE FROM replay_allocation")
            conn.execute("DELETE FROM replay_lease")
            if saved.interval is not None:
                for product, mw in saved.desired_mw.items():
                    conn.execute(
                        "INSERT INTO replay_desired (product, mw, interval) VALUES (%s, %s, %s)",
                        (product, mw, saved.interval),
                    )
            for agent, target in saved.allocations.items():
                conn.execute(
                    "INSERT INTO replay_allocation (agent_id, target_kw, version) VALUES (%s, %s, %s)",
                    (agent, target, saved.versions.get(agent, 0)),
                )
            for agent, heartbeat in saved.leases.items():
                conn.execute(
                    "INSERT INTO replay_lease (agent_id, heartbeat_s) VALUES (%s, %s)",
                    (agent, heartbeat),
                )

    def _ensure(self) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_desired (
                    product text PRIMARY KEY,
                    mw double precision NOT NULL,
                    interval integer NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_allocation (
                    agent_id integer PRIMARY KEY,
                    target_kw double precision NOT NULL,
                    version bigint NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_lease (
                    agent_id integer PRIMARY KEY,
                    heartbeat_s double precision NOT NULL
                )
                """)
