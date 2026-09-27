"""Deployment allocation for one live-replay tick.

A deployment commands an award in MW. The coordinator splits that across
agents that heartbeated, in proportion to available kW, after giving credit
for the kW they are already delivering. Backup-mode and stale homes
contribute nothing. Agents keep a setpoint until a newer command replaces it,
and a tick discharges that setpoint once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from harness.products import PRODUCTS
from harness.scenario import Scenario


def award_mw(reported_mw: Mapping[str, float], deployed: Mapping[str, Any], interval: int,
             scenario: Scenario) -> dict[str, float]:
    """MW to command for each product at `interval`: the award, or zero."""
    commanded = {}
    for product in PRODUCTS:
        if not bool(deployed[product][interval]):
            commanded[product] = 0.0
            continue
        reported = max(0.0, float(reported_mw[product]))
        commanded[product] = min(reported, scenario.products[product].award_limit_mw)
    return commanded


def home_available_kw(reading: Mapping[str, Any], t_s: float, stale_s: float, floor: float,
                      battery_kwh: float, inverter_kw: float, duration_h: float) -> float:
    """kW this home can sustain, or zero when it is in backup or its reading is stale."""
    if bool(reading["backup_mode"]) or not bool(reading["available"]):
        return 0.0
    if t_s - float(reading["t_s"]) > stale_s:
        return 0.0
    energy_kwh = max(float(reading["soc"]) - floor, 0.0) * battery_kwh
    return min(inverter_kw, energy_kwh / duration_h)


def allocate(desired_kw: float, live: Mapping[int, tuple[float, float]]) -> dict[int, float]:
    """Target kW per live agent, closing the gap to `desired_kw`.

    `live` maps an agent id to `(available_kw, delivered_kw)`. Agents with no
    available kW, including those whose homes are in backup or stale, are set
    to zero. The gap between desired kW and what the remaining agents are
    delivering is shared in proportion to available kW.
    """
    targets = {agent: 0.0 for agent in live}
    if not live or desired_kw <= 0:
        return targets
    useful = {
        agent: (available, delivered)
        for agent, (available, delivered) in live.items()
        if available > 0
    }
    total = sum(available for available, _delivered in useful.values())
    if total <= 0:
        return targets
    gap = desired_kw - sum(delivered for _available, delivered in useful.values())
    for agent, (available, delivered) in useful.items():
        targets[agent] = max(0.0, delivered + gap * available / total)
    return targets


def attribute_delivery(delivered_kw: float, targets_kw: Mapping[str, float]) -> dict[str, float]:
    """Split one agent's delivered kW across products by its last targets."""
    total = sum(targets_kw.values())
    if total <= 0:
        return {product: 0.0 for product in PRODUCTS}
    return {product: delivered_kw * float(targets_kw.get(product, 0.0)) / total for product in PRODUCTS}


def commands_for(targets_kw: Mapping[int, float], versions: dict[int, int],
                 last_kw: dict[int, float]) -> list[dict[str, float | int]]:
    """A new version for every live agent each tick.

    Repeating the command is what lets a restarted coordinator be heard. The
    version comes from the last one stored, so the repeat is newer than the
    command the agent already applied.
    """
    commands: list[dict[str, float | int]] = []
    for agent, target in targets_kw.items():
        versions[agent] = versions.get(agent, 0) + 1
        last_kw[agent] = target
        commands.append({"agent_id": agent, "version": versions[agent], "target_kw": target})
    return commands


def apply_commands(version: int, setpoint_kw: float,
                   commands: Sequence[Mapping[str, Any]]) -> tuple[int, float, int, bool]:
    """Replace the setpoint only when the command version is newer.

    The tick discharges that setpoint once, and only if a newer command was
    accepted. A duplicate or an older command is not applied, so it cannot
    discharge again.
    """
    extras = 0
    accepted = False
    for command in commands:
        command_version = int(command["version"])
        if command_version > version:
            if accepted:
                extras += 1
            version = command_version
            setpoint_kw = float(command["target_kw"])
            accepted = True
    return version, setpoint_kw, extras, accepted


def discharge(home_ids: Any, setpoint_kw: float, soc: Any, online: Any, backup: Any,
              tick_s: float, *, battery_kwh: float, inverter_kw: float,
              backup_floor: float) -> tuple[float, int]:
    """Discharge `setpoint_kw` for one tick. Returns delivered kW and floor breaches.

    Power is shared across homes that are online and not in backup, in
    proportion to the energy they can release, and clamped at the backup floor.
    A breach is a home whose requested energy exceeded what is above that floor.
    """
    if setpoint_kw <= 0 or tick_s <= 0:
        return 0.0, 0
    hours = tick_s / 3600.0
    eligible: list[tuple[int, float]] = []
    for home in home_ids:
        if not bool(online[home]) or bool(backup[home]):
            continue
        above = max(float(soc[home]) - backup_floor, 0.0) * battery_kwh
        if above <= 0:
            continue
        eligible.append((int(home), above))
    if not eligible:
        return 0.0, 0
    weights = [min(inverter_kw, above) for _, above in eligible]
    total = sum(weights)
    taken_kwh = 0.0
    breaches = 0
    for (home, above), weight in zip(eligible, weights):
        requested = setpoint_kw * weight / total * hours
        if requested > above + 1e-6:
            breaches += 1
        taken = min(requested, above)
        soc[home] = backup_floor + (above - taken) / battery_kwh
        taken_kwh += taken
    return taken_kwh / hours, breaches


def mw_from_kw(commanded_mw: Mapping[str, float], delivered_kw: float) -> dict[str, float]:
    """Attribute delivered kW back to MW, in proportion to the commanded MW."""
    commanded_kw = {product: float(commanded_mw[product]) * 1000.0 for product in PRODUCTS}
    total = sum(commanded_kw.values())
    if total <= 0 or delivered_kw <= 0:
        return {product: 0.0 for product in PRODUCTS}
    return {product: delivered_kw * commanded_kw[product] / total / 1000.0 for product in PRODUCTS}
