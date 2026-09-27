"""Agent-host and coordinator processes for one live replay.

Spawn imports this module in each child. Hosts report one batched heartbeat
per tick. The coordinator turns fresh telemetry into the backtest observation
and asks the policy for capability. During a deployment it commands a share of
the award; hosts discharge that setpoint and report the kW they delivered.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from harness.fleet import FleetCase
from harness.observation import observations
from harness.products import PRODUCTS
from harness.replay.control import (
    allocate,
    apply_commands,
    attribute_delivery,
    award_mw,
    commands_for,
    discharge,
    home_available_kw,
    mw_from_kw,
)
from harness.replay.store import MemoryStore, PostgresStore, SavedCoordinator
from harness.rng import RandomStreams
from harness.scenario import Scenario


def interval_at(starts_ns: np.ndarray, t_s: float) -> int:
    """The market interval covering `t_s` seconds after the day's first interval."""
    when = int(starts_ns[0]) + int(round(t_s * 1_000_000_000))
    index = int(np.searchsorted(starts_ns, when, side="right") - 1)
    return min(max(index, 0), len(starts_ns) - 1)


def run_host(region: int, conn: Any, home_ids: np.ndarray, soc: np.ndarray,
             online: np.ndarray, backup: np.ndarray, starts_ns: np.ndarray,
             battery_kwh: float, inverter_kw: float, backup_floor: float) -> None:
    """One failure domain: heartbeats out, and a setpoint applied once per newer command."""
    version = 0
    setpoint_kw = 0.0
    delivered_kw = 0.0
    double_discharges = 0
    floor_breaches = 0
    soc = np.array(soc, dtype=float, copy=True)
    try:
        while True:
            message = conn.recv()
            if message is None:
                return
            kind, t_s = message[0], float(message[1])
            interval = interval_at(starts_ns, t_s)
            if kind == "apply":
                version, setpoint_kw, extras, accepted = apply_commands(version, setpoint_kw, message[2])
                double_discharges += extras
                if accepted:
                    delivered_kw, breaches = discharge(
                        home_ids, setpoint_kw, soc, online[:, interval], backup[:, interval],
                        float(message[3]), battery_kwh=battery_kwh, inverter_kw=inverter_kw,
                        backup_floor=backup_floor,
                    )
                    floor_breaches += breaches
                else:
                    delivered_kw = 0.0
            batch = _batch(region, home_ids, soc, online[:, interval], backup[:, interval], t_s)
            batch["delivered_kw"] = delivered_kw
            batch["version"] = version
            batch["double_discharges"] = double_discharges
            batch["floor_breaches"] = floor_breaches
            conn.send(batch)
    finally:
        conn.close()


def run_coordinator(conn: Any, policy: Any, scenario: Scenario, rows: pd.DataFrame,
                    deliverable_mw: dict[str, np.ndarray], home_stale: np.ndarray,
                    starts_ns: np.ndarray, seed: int, day: Any,
                    deployed: dict[str, np.ndarray], dsn: str | None = None) -> None:
    """Fresh telemetry in, commands out, one timeline row per tick.

    Durable state is loaded before the first tick. Stored leases do not make
    an agent live: that happens on a heartbeat to this process.
    """
    begin_day = getattr(policy, "begin_day", None)
    if callable(begin_day):
        begin_day()
    arm = getattr(policy, "arm", None)
    if callable(arm):
        arm(RandomStreams(scenario.name, seed), day)
    store = PostgresStore(dsn) if dsn else MemoryStore()
    saved = store.load()
    last: dict[int, dict[str, Any]] = {}
    ticks: list[dict[str, Any]] = []
    versions = dict(saved.versions)
    last_kw = dict(saved.allocations)
    product_kw: dict[int, dict[str, float]] = {}
    award = dict(saved.desired_mw) if saved.interval is not None else None
    award_interval = -1 if saved.interval is None else saved.interval
    # `saved.leases` is deliberately unused: agents are unknown until they heartbeat.
    pending: dict[str, Any] | None = None
    double_discharges = 0
    floor_breaches = 0
    try:
        while True:
            message = conn.recv()
            if message is None:
                conn.send({
                    "ticks": ticks,
                    "double_discharges": double_discharges,
                    "floor_breaches": floor_breaches,
                })
                return
            if message[0] == "flush":
                conn.send({
                    "ticks": ticks,
                    "double_discharges": double_discharges,
                    "floor_breaches": floor_breaches,
                })
                ticks = []
                continue
            if message[0] == "applied":
                reports = message[1]
                delivered_kw = sum(float(report["delivered_kw"]) for report in reports)
                double_discharges = sum(int(report["double_discharges"]) for report in reports)
                floor_breaches = sum(int(report["floor_breaches"]) for report in reports)
                assert pending is not None
                ticks.append({
                    **pending,
                    "delivered_mw": mw_from_kw(pending["commanded_mw"], delivered_kw),
                })
                pending = None
                continue
            _, t_s, batches = message
            t_s = float(t_s)
            for batch in batches:
                for reading in batch["telemetry"]:
                    last[int(reading["home_id"])] = reading
            interval = interval_at(starts_ns, t_s)
            observation = _observation(scenario, rows, last, t_s, interval, home_stale[:, interval])
            decision = policy.decide(observation)
            reported = {product: float(decision[product]) for product in PRODUCTS}
            if interval != award_interval or award is None:
                award = award_mw(reported, deployed, interval, scenario)
                award_interval = interval
            targets = _targets(scenario, award, batches, t_s, product_kw)
            pending = {
                "t_s": t_s,
                "reported_mw": reported,
                "deliverable_mw": {product: float(deliverable_mw[product][interval]) for product in PRODUCTS},
                "commanded_mw": dict(award),
            }
            issued = commands_for(targets, versions, last_kw)
            for agent in list(last_kw):
                if agent not in targets:
                    last_kw[agent] = 0.0
            store.save(SavedCoordinator(
                desired_mw=dict(award),
                interval=award_interval,
                allocations=dict(last_kw),
                versions=dict(versions),
                leases={int(batch["region"]): t_s for batch in batches},
            ))
            conn.send(issued)
    finally:
        close = getattr(policy, "close", None)
        if callable(close):
            close()
        conn.close()


def _targets(scenario: Scenario, award: dict[str, float], batches: list[dict[str, Any]],
             t_s: float, product_kw: dict[int, dict[str, float]]) -> dict[int, float]:
    """Per-agent setpoint in kW: each deployed product's award, closed against delivery."""
    fleet = scenario.fleet
    live: dict[int, tuple[dict[str, float], float]] = {}
    for batch in batches:
        region = int(batch["region"])
        available = {
            product: sum(
                home_available_kw(
                    reading, t_s, fleet.telemetry_stale_s, fleet.backup_floor, fleet.battery_kwh,
                    fleet.inverter_kw, scenario.products[product].duration_h,
                )
                for reading in batch["telemetry"]
            )
            for product in PRODUCTS
        }
        live[region] = (available, float(batch["delivered_kw"]))
    totals = {region: 0.0 for region in live}
    for product in PRODUCTS:
        desired_kw = award[product] * 1000.0
        views = {}
        for region, (available, delivered_kw) in live.items():
            portions = attribute_delivery(delivered_kw, product_kw.get(region, {}))
            views[region] = (available[product], portions[product])
        shares = allocate(desired_kw, views)
        for region, kw in shares.items():
            totals[region] += kw
            product_kw.setdefault(region, {})[product] = kw
    return totals


def _batch(region: int, home_ids: np.ndarray, soc: np.ndarray, online: np.ndarray,
           backup: np.ndarray, t_s: float) -> dict[str, Any]:
    telemetry: list[dict[str, Any]] = []

    async def one(home: int) -> None:
        telemetry.append({
            "home_id": home,
            "soc": float(soc[home]),
            "backup_mode": bool(backup[home]),
            "available": bool(online[home]),
            "t_s": t_s,
        })

    async def gather() -> None:
        async with asyncio.TaskGroup() as tasks:
            for home in home_ids:
                tasks.create_task(one(int(home)))

    asyncio.run(gather())
    telemetry.sort(key=lambda reading: int(reading["home_id"]))
    return {"region": region, "t_s": t_s, "telemetry": telemetry}


def _observation(scenario: Scenario, rows: pd.DataFrame, last: dict[int, dict[str, Any]],
                 t_s: float, interval: int, stale_truth: np.ndarray):
    """The backtest observation, with the fleet taken from telemetry still in date."""
    fleet = scenario.fleet
    region_of = np.arange(fleet.homes) % fleet.regions
    by_region = np.eye(fleet.regions)[region_of]
    soc = np.zeros(fleet.homes)
    online = np.zeros(fleet.homes, dtype=bool)
    backup = np.zeros(fleet.homes, dtype=bool)
    stale = np.ones(fleet.homes, dtype=bool)
    for home, reading in last.items():
        if t_s - float(reading["t_s"]) > fleet.telemetry_stale_s:
            continue
        stale[home] = False
        soc[home] = float(reading["soc"])
        if reading["backup_mode"]:
            backup[home] = True
        elif reading["available"]:
            online[home] = True
        elif bool(stale_truth[home]):
            stale[home] = True
    seen = online.astype(float)[None, :]
    energy = np.maximum(soc - fleet.backup_floor, 0.0) * fleet.battery_kwh
    case = FleetCase(
        name="telemetry",
        homes=np.broadcast_to(by_region.sum(axis=0), (1, fleet.regions)).copy(),
        homes_online=(seen @ by_region),
        homes_stale=stale.astype(float)[None, :] @ by_region,
        homes_backup=backup.astype(float)[None, :] @ by_region,
        energy_above_floor_kwh=(seen * energy) @ by_region,
        inverter_kw=(seen * fleet.inverter_kw) @ by_region,
        capability_kw={
            product: (seen * np.minimum(fleet.inverter_kw, energy / rules.duration_h)) @ by_region
            for product, rules in scenario.products.items()
        },
        deliverable_mw={product: np.zeros(1) for product in PRODUCTS},
        home_soc=soc,
        home_online=online[:, None],
        sustains={product: np.ones((fleet.homes, 1), dtype=bool) for product in PRODUCTS},
        region_of=region_of,
        home_backup=backup[:, None],
        home_stale=stale[:, None],
    )
    return next(observations(rows.iloc[[interval]], scenario, case))
