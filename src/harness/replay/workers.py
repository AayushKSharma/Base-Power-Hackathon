"""Agent-host and coordinator processes for one live replay.

Spawn imports this module in each child. Hosts report one batched heartbeat
per tick. The coordinator turns fresh telemetry into the backtest observation
and asks the policy for capability.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from harness.fleet import FleetCase
from harness.observation import observations
from harness.products import PRODUCTS
from harness.rng import RandomStreams
from harness.scenario import Scenario


def interval_at(starts_ns: np.ndarray, t_s: float) -> int:
    """The market interval covering `t_s` seconds after the day's first interval."""
    when = int(starts_ns[0]) + int(round(t_s * 1_000_000_000))
    index = int(np.searchsorted(starts_ns, when, side="right") - 1)
    return min(max(index, 0), len(starts_ns) - 1)


def run_host(region: int, conn: Any, home_ids: np.ndarray, soc: np.ndarray,
             online: np.ndarray, backup: np.ndarray, starts_ns: np.ndarray) -> None:
    """One failure domain: each of its homes reports as an async task."""
    try:
        while True:
            message = conn.recv()
            if message is None:
                return
            t_s = float(message)
            interval = interval_at(starts_ns, t_s)
            conn.send(_batch(region, home_ids, soc, online[:, interval], backup[:, interval], t_s))
    finally:
        conn.close()


def run_coordinator(conn: Any, policy: Any, scenario: Scenario, rows: pd.DataFrame,
                    deliverable_mw: dict[str, np.ndarray], home_stale: np.ndarray,
                    starts_ns: np.ndarray, seed: int, day: Any) -> None:
    """Fresh telemetry in, one timeline row per tick out."""
    begin_day = getattr(policy, "begin_day", None)
    if callable(begin_day):
        begin_day()
    arm = getattr(policy, "arm", None)
    if callable(arm):
        arm(RandomStreams(scenario.name, seed), day)
    last: dict[int, dict[str, Any]] = {}
    ticks: list[dict[str, Any]] = []
    try:
        while True:
            message = conn.recv()
            if message is None:
                conn.send(ticks)
                return
            t_s, batches = message
            for batch in batches:
                for reading in batch["telemetry"]:
                    last[int(reading["home_id"])] = reading
            interval = interval_at(starts_ns, float(t_s))
            observation = _observation(scenario, rows, last, float(t_s), interval, home_stale[:, interval])
            decision = policy.decide(observation)
            ticks.append({
                "t_s": float(t_s),
                "reported_mw": {product: float(decision[product]) for product in PRODUCTS},
                "deliverable_mw": {product: float(deliverable_mw[product][interval]) for product in PRODUCTS},
            })
    finally:
        close = getattr(policy, "close", None)
        if callable(close):
            close()
        conn.close()


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
