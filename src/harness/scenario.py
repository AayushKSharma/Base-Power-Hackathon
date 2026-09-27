"""Scenario config: every assumption a harness run depends on, in one YAML file.

See scenarios/baseline.yaml for every field with its documented default.

Every field is required and unknown fields are rejected, so a typo never falls
back silently to a default. A scenario carries both fleet-state models, the
quantile mock (`fleet.quantile_mock`) and the failure model (`failures`), and
`fleet.state` picks the one that drives the run, so switching modes is a
one-line change. The scenario's name is its file name.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from harness.market.catalog import CPT
from harness.products import LOAD_ZONES, PRODUCTS

# Fleet-state modes.
QUANTILE = "quantile"  # quantile mocks: the share of the fleet available, P10..P90
STOCHASTIC = "stochastic"  # correlated failure model: dropouts and regional outages
QUANTILES = ("P10", "P25", "P50", "P75", "P90")

# What the policy sees in quantile mode.
TYPICAL = "typical"  # the typical quantile's fleet; its K is scored against every quantile's D
PER_CASE = "per_case"  # each quantile's own fleet, deciding once per quantile

# How a shortfall is turned into dollars. These are assumptions; the physical
# shortfall MW-h does not depend on them.
ENERGY = "energy"  # (a) load-zone RT price x shortfall x product duration
ENERGY_SPD = "energy_spd"  # (b) (a), plus Set Point Deviation beyond tolerance
IMBALANCE = "imbalance"  # (c) shortfall x RT MCPC x interval length
PRESETS = (ENERGY, ENERGY_SPD, IMBALANCE)

# Set Point Deviation tolerance: the lesser of 3% of the award or 3 MW.
SPD_TOLERANCE_FRACTION = 0.03
SPD_TOLERANCE_MW = 3.0


class ScenarioError(ValueError):
    """An invalid scenario; the message names the offending field."""


@dataclass(frozen=True)
class ProductRules:
    duration_h: float  # how long a deployment must be sustained
    cap_mw: float  # pilot cap, system-wide MW for the product
    cap_share: float  # largest share of the cap one QSE may hold

    @property
    def award_limit_mw(self) -> float:
        return self.cap_mw * self.cap_share


@dataclass(frozen=True)
class Soc:
    """Each home's state of charge for a day: `fixed`, or drawn from Beta(a, b)."""

    fixed: float | None = None
    beta: tuple[float, float] | None = None

    def draw(self, homes: int, rng: np.random.Generator) -> np.ndarray:
        if self.beta is None:
            return np.full(homes, self.fixed)
        return rng.beta(*self.beta, homes)


@dataclass(frozen=True, eq=False)
class QuantileMock:
    """Share of the fleet's homes available at each quantile, by CPT month and hour,
    and what the policy sees of it.

    TYPICAL: the policy sees the `typical` quantile's fleet, and its one decision
    per interval is scored against every quantile's true D. That measures
    planning uncertainty: reality turning out worse (or better) than the fleet
    the policy expected. PER_CASE: the policy sees each quantile's own fleet
    and decides for each, so it only over-sells by reporting more than it sees.
    """

    shares: dict[str, np.ndarray]  # quantile -> array of shape (12 months, 24 hours)
    policy_view: str  # TYPICAL or PER_CASE
    typical: str  # the quantile the policy sees in the TYPICAL view

    def share(self, quantile: str, month: np.ndarray, hour: np.ndarray) -> np.ndarray:
        """Shares for CPT months (1-12) and hours (0-23)."""
        return self.shares[quantile][month - 1, hour]


@dataclass(frozen=True)
class Fleet:
    homes: int
    regions: int  # failure domains; home i is in region i % regions
    battery_kwh: float
    inverter_kw: float
    backup_floor: float  # SOC share kept for the member's backup
    soc: Soc
    telemetry_stale_s: float  # telemetry older than this is stale
    state: str  # QUANTILE or STOCHASTIC: which fleet-state model drives the run
    quantile_mock: QuantileMock  # used in QUANTILE mode


@dataclass(frozen=True)
class RegionOutage:
    """A regional grid outage forced at a set time (for storms and test fixtures)."""

    region: int
    start: pd.Timestamp  # tz-aware, CPT
    minutes: float


@dataclass(frozen=True)
class Failures:
    """The stochastic fleet-state model, used in STOCHASTIC mode."""

    home_dropout_per_h: float  # chance a home loses telemetry and control in an hour
    home_dropout_min: float
    region_outage_per_h: float  # chance a region's grid goes down in an hour
    region_outage_min: float
    scarcity_stress: float  # both chances are multiplied by this in scarce intervals
    forced_region_outages: tuple[RegionOutage, ...]


@dataclass(frozen=True)
class ForcedDeployment:
    """A deployment forced at a set time (for storms and test fixtures)."""

    product: str
    start: pd.Timestamp  # tz-aware, CPT
    minutes: float


@dataclass(frozen=True)
class Deployments:
    """Whether ERCOT calls on a product this interval.

    `calm` and `scarce` are probabilities per interval, chosen from the
    dataset's scarcity flag. Draws use the seeded "deployments" stream, so
    every policy sees the same calls. `refill_kw` is how fast an online home
    recharges between those calls.
    """

    calm: float
    scarce: float
    refill_kw: float
    forced: tuple[ForcedDeployment, ...]


@dataclass(frozen=True)
class Scoring:
    """Secondary dollar views. The physical shortfall does not depend on them.

    `preset` selects the shortfall-cost formula. `compliance_per_mw` is added
    on top, per MW short, under every preset. `exceedance_mw` and `tolerance_mw`
    are the MW grids of the exceedance curve and the tolerance table.
    """

    preset: str
    load_zone: str  # which load zone's RT price is λ
    compliance_per_mw: float
    spd_per_mwh: float  # Set Point Deviation rate, preset (b) only
    exceedance_mw: tuple[float, ...]
    tolerance_mw: tuple[float, ...]


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int  # default seed for runs that don't pass one
    fleet: Fleet
    failures: Failures
    deployments: Deployments
    scoring: Scoring
    products: dict[str, ProductRules]


def resolve_scenario(spec: str | Path) -> Path:
    """A scenario path. A bare preset name is the file of that name under scenarios/."""
    path = Path(spec)
    if path.is_file():
        return path
    bundled = Path(__file__).resolve().parents[2] / "scenarios"
    for candidate in (bundled / spec, bundled / f"{path.name}.yaml"):
        if candidate.is_file():
            return candidate
    return path


def load_scenario(path: Path | str) -> Scenario:
    """Read and validate a scenario YAML file. Raises ScenarioError."""
    path = Path(path)
    try:
        data = yaml.load(path.read_text(), Loader=_StrictLoader)
        data = _apply_extends(data, path.parent, trail=(path.resolve(),))
        return parse_scenario(data, name=path.stem, base_dir=path.parent)
    except OSError as e:
        raise ScenarioError(f"{path}: cannot read: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ScenarioError(f"{path}: not valid YAML: {e}") from e
    except ScenarioError as e:
        raise ScenarioError(f"{path}: {e}") from e


def _apply_extends(data: Any, directory: Path, trail: tuple[Path, ...]) -> Any:
    """Overlay `data` on the scenario it names in `extends`, resolved beside the file.

    Mappings merge key by key. Everything else, including lists, is replaced.
    The scenario's name stays the overlay file's name.
    """
    if not isinstance(data, dict) or "extends" not in data:
        return data
    parent_name = data["extends"]
    if not isinstance(parent_name, str) or not parent_name:
        raise ScenarioError(f"extends: expected a scenario name, got {parent_name!r}")
    parent_path = (directory / f"{parent_name}.yaml").resolve()
    if parent_path in trail:
        raise ScenarioError(f"extends: {parent_name} includes itself")
    try:
        parent = yaml.load(parent_path.read_text(), Loader=_StrictLoader)
    except OSError as e:
        raise ScenarioError(f"extends: cannot read {parent_name}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ScenarioError(f"extends: {parent_name} is not valid YAML: {e}") from e
    parent = _apply_extends(parent, parent_path.parent, trail + (parent_path,))
    overlay = {key: value for key, value in data.items() if key != "extends"}
    return _deep_merge(parent, overlay)


def _deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return overlay


class _StrictLoader(yaml.SafeLoader):
    """YAML's safe loader, except a key given twice in one mapping is an error
    (plain YAML keeps the last one)."""


def _mapping_without_duplicates(loader: _StrictLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, Hashable):
            continue  # construct_mapping reports it
        if key in seen:
            raise ScenarioError(f"duplicate field {key!r} on line {key_node.start_mark.line + 1}")
        seen.add(key)
    return loader.construct_mapping(node)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)


def parse_scenario(data: Any, name: str, base_dir: Path | None = None) -> Scenario:
    """Validate an already-parsed scenario mapping. Raises ScenarioError.

    `base_dir` is where a quantile-mock CSV path is resolved from (default: the
    working directory).
    """
    root = _exact_fields(data, "", ("seed", "fleet", "failures", "deployments", "scoring", "products"))
    fleet = _fleet(root["fleet"], "fleet", base_dir)
    products = _exact_fields(root["products"], "products", tuple(PRODUCTS))
    return Scenario(
        name=name,
        seed=_seed(root["seed"], "seed"),
        fleet=fleet,
        failures=_failures(root["failures"], "failures", fleet),
        deployments=_deployments(root["deployments"], "deployments"),
        scoring=_scoring(root["scoring"], "scoring"),
        products={p: _product_rules(products[p], f"products.{p}") for p in PRODUCTS},
    )


def _fleet(value: Any, where: str, base_dir: Path | None) -> Fleet:
    fleet = _exact_fields(value, where, ("homes", "regions", "battery_kwh", "inverter_kw", "backup_floor",
                                         "soc", "telemetry_stale_s", "state", "quantile_mock"))
    homes = _integer(fleet["homes"], f"{where}.homes", low=1)
    regions = _integer(fleet["regions"], f"{where}.regions", low=1)
    if regions > homes:
        raise ScenarioError(f"{where}.regions: cannot exceed homes ({homes}), got {regions}")
    if fleet["state"] not in (QUANTILE, STOCHASTIC):
        raise ScenarioError(f"{where}.state: expected {QUANTILE} or {STOCHASTIC}, got {fleet['state']!r}")
    return Fleet(
        homes=homes,
        regions=regions,
        battery_kwh=_number(fleet["battery_kwh"], f"{where}.battery_kwh", low=0),
        inverter_kw=_number(fleet["inverter_kw"], f"{where}.inverter_kw", low=0),
        backup_floor=_number(fleet["backup_floor"], f"{where}.backup_floor", low=0, high=1),
        soc=_soc(fleet["soc"], f"{where}.soc"),
        telemetry_stale_s=_number(fleet["telemetry_stale_s"], f"{where}.telemetry_stale_s", low=0),
        state=fleet["state"],
        quantile_mock=_quantile_mock(fleet["quantile_mock"], f"{where}.quantile_mock", base_dir),
    )


def _soc(value: Any, where: str) -> Soc:
    if isinstance(value, Mapping) and set(value) == {"fixed"}:
        return Soc(fixed=_number(value["fixed"], f"{where}.fixed", low=0, high=1))
    if isinstance(value, Mapping) and set(value) == {"beta"}:
        ab = value["beta"]
        if not isinstance(ab, list) or len(ab) != 2:
            raise ScenarioError(f"{where}.beta: expected [a, b], got {ab!r}")
        a, b = (_number(x, f"{where}.beta", low=0) for x in ab)
        if a == 0 or b == 0:
            raise ScenarioError(f"{where}.beta: a and b must be positive, got {ab!r}")
        return Soc(beta=(a, b))
    raise ScenarioError(f"{where}: expected {{fixed: <SOC 0-1>}} or {{beta: [a, b]}}, got {value!r}")


def _quantile_mock(value: Any, where: str, base_dir: Path | None) -> QuantileMock:
    mock = _exact_fields(value, where, ("shares", "policy_view", "typical"))
    if mock["policy_view"] not in (TYPICAL, PER_CASE):
        raise ScenarioError(f"{where}.policy_view: expected {TYPICAL} or {PER_CASE}, got {mock['policy_view']!r}")
    if mock["typical"] not in QUANTILES:
        raise ScenarioError(f"{where}.typical: expected one of {', '.join(QUANTILES)}, got {mock['typical']!r}")
    return QuantileMock(_shares(mock["shares"], f"{where}.shares", base_dir),
                        policy_view=mock["policy_view"], typical=mock["typical"])


def _shares(value: Any, where: str, base_dir: Path | None) -> dict[str, np.ndarray]:
    """Flat shares per quantile, or a CSV path with month,hour,P10,...,P90 columns."""
    if isinstance(value, str):
        path = (base_dir or Path.cwd()) / value
        shares = _quantile_csv(path, f"{where} ({path})")
    else:
        flat = _exact_fields(value, where, QUANTILES)
        shares = {q: np.full((12, 24), _number(flat[q], f"{where}.{q}", low=0, high=1)) for q in QUANTILES}
    stacked = np.stack([shares[q] for q in QUANTILES])
    falling = np.argwhere(np.diff(stacked, axis=0) < 0)
    if len(falling):
        _, month, hour = falling[0]
        raise ScenarioError(f"{where}: shares must not fall from P10 to P90 "
                            f"(month {month + 1}, hour {hour})")
    return shares


def _quantile_csv(path: Path, where: str) -> dict[str, np.ndarray]:
    try:
        table = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ScenarioError(f"{where}: cannot read: {e}") from e
    expected = ["month", "hour", *QUANTILES]
    if list(table.columns) != expected:
        raise ScenarioError(f"{where}: expected columns {','.join(expected)}, got {','.join(table.columns)}")
    buckets = pd.MultiIndex.from_product([range(1, 13), range(24)], names=["month", "hour"])
    table = table.set_index(["month", "hour"])
    if len(table) != len(buckets) or not table.index.sort_values().equals(buckets):
        raise ScenarioError(f"{where}: expected one row per month 1-12 and hour 0-23")
    values = table.reindex(buckets)[list(QUANTILES)].apply(pd.to_numeric, errors="coerce").to_numpy()
    if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ScenarioError(f"{where}: shares must be numbers between 0 and 1")
    return {q: values[:, i].reshape(12, 24) for i, q in enumerate(QUANTILES)}


def _failures(value: Any, where: str, fleet: Fleet) -> Failures:
    failures = _exact_fields(value, where, (
        "home_dropout_per_h", "home_dropout_min", "region_outage_per_h", "region_outage_min",
        "scarcity_stress", "forced_region_outages"))
    forced = failures["forced_region_outages"]
    if not isinstance(forced, list):
        raise ScenarioError(f"{where}.forced_region_outages: expected a list, got {forced!r}")
    return Failures(
        home_dropout_per_h=_number(failures["home_dropout_per_h"], f"{where}.home_dropout_per_h", low=0, high=1),
        home_dropout_min=_number(failures["home_dropout_min"], f"{where}.home_dropout_min", low=0),
        region_outage_per_h=_number(failures["region_outage_per_h"], f"{where}.region_outage_per_h",
                                    low=0, high=1),
        region_outage_min=_number(failures["region_outage_min"], f"{where}.region_outage_min", low=0),
        scarcity_stress=_number(failures["scarcity_stress"], f"{where}.scarcity_stress", low=0),
        forced_region_outages=tuple(_region_outage(o, f"{where}.forced_region_outages[{i}]", fleet)
                                    for i, o in enumerate(forced)),
    )


def _region_outage(value: Any, where: str, fleet: Fleet) -> RegionOutage:
    outage = _exact_fields(value, where, ("region", "start", "minutes"))
    region = _integer(outage["region"], f"{where}.region", low=0)
    if region >= fleet.regions:
        raise ScenarioError(f"{where}.region: must be below fleet.regions ({fleet.regions}), got {region}")
    return RegionOutage(region, _cpt_time(outage["start"], f"{where}.start"),
                        _number(outage["minutes"], f"{where}.minutes", low=0))


def _deployments(value: Any, where: str) -> Deployments:
    raw = _exact_fields(value, where, ("calm", "scarce", "refill_kw", "forced"))
    forced = raw["forced"]
    if not isinstance(forced, list):
        raise ScenarioError(f"{where}.forced: expected a list, got {forced!r}")
    return Deployments(
        calm=_number(raw["calm"], f"{where}.calm", low=0, high=1),
        scarce=_number(raw["scarce"], f"{where}.scarce", low=0, high=1),
        refill_kw=_number(raw["refill_kw"], f"{where}.refill_kw", low=0),
        forced=tuple(_forced_deployment(item, f"{where}.forced[{i}]") for i, item in enumerate(forced)),
    )


def _forced_deployment(value: Any, where: str) -> ForcedDeployment:
    raw = _exact_fields(value, where, ("product", "start", "minutes"))
    if raw["product"] not in PRODUCTS:
        raise ScenarioError(f"{where}.product: expected one of {', '.join(PRODUCTS)}, got {raw['product']!r}")
    return ForcedDeployment(raw["product"], _cpt_time(raw["start"], f"{where}.start"),
                            _number(raw["minutes"], f"{where}.minutes", low=0))


def _scoring(value: Any, where: str) -> Scoring:
    raw = _exact_fields(value, where, (
        "preset", "load_zone", "compliance_per_mw", "spd_per_mwh", "exceedance_mw", "tolerance_mw"))
    if raw["preset"] not in PRESETS:
        raise ScenarioError(f"{where}.preset: expected one of {', '.join(PRESETS)}, got {raw['preset']!r}")
    if raw["load_zone"] not in LOAD_ZONES:
        raise ScenarioError(f"{where}.load_zone: expected one of {', '.join(LOAD_ZONES)}, "
                            f"got {raw['load_zone']!r}")
    return Scoring(
        preset=raw["preset"],
        load_zone=raw["load_zone"],
        compliance_per_mw=_number(raw["compliance_per_mw"], f"{where}.compliance_per_mw", low=0),
        spd_per_mwh=_number(raw["spd_per_mwh"], f"{where}.spd_per_mwh", low=0),
        exceedance_mw=_mw_grid(raw["exceedance_mw"], f"{where}.exceedance_mw"),
        tolerance_mw=_mw_grid(raw["tolerance_mw"], f"{where}.tolerance_mw"),
    )


def _mw_grid(value: Any, where: str) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ScenarioError(f"{where}: expected a non-empty list of MW, got {value!r}")
    return tuple(_number(x, where, low=0) for x in value)


def _cpt_time(value: Any, where: str) -> pd.Timestamp:
    try:
        start = pd.Timestamp(value)
        return start.tz_localize(CPT) if start.tzinfo is None else start.tz_convert(CPT)
    except Exception as e:  # unparseable, or a local time DST skips or repeats
        raise ScenarioError(f"{where}: expected a CPT date and time like '2026-03-08 14:00' "
                            f"(give a UTC offset around DST changes), got {value!r}") from e


def _product_rules(value: Any, where: str) -> ProductRules:
    rules = _exact_fields(value, where, ("duration_h", "cap_mw", "cap_share"))
    duration = _number(rules["duration_h"], f"{where}.duration_h", low=0)
    if duration == 0:
        raise ScenarioError(f"{where}.duration_h: must be positive")
    return ProductRules(
        duration_h=duration,
        cap_mw=_number(rules["cap_mw"], f"{where}.cap_mw", low=0),
        cap_share=_number(rules["cap_share"], f"{where}.cap_share", low=0, high=1),
    )


def _exact_fields(value: Any, where: str, names: tuple[str, ...]) -> Mapping[str, Any]:
    """`value` as a mapping with exactly the fields `names`."""
    prefix = f"{where}: " if where else ""
    if not isinstance(value, Mapping):
        raise ScenarioError(f"{prefix}expected a mapping of {', '.join(names)}, got {value!r}")
    for problem, keys in (("unknown", [k for k in value if k not in names]),
                          ("missing", [k for k in names if k not in value])):
        if keys:
            plural = "s" if len(keys) > 1 else ""
            raise ScenarioError(f"{prefix}{problem} field{plural} {', '.join(map(repr, keys))}; "
                                f"expected {', '.join(names)}")
    return value


def _number(value: Any, where: str, low: float, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ScenarioError(f"{where}: expected a number, got {value!r}")
    if high is not None and not low <= value <= high:
        raise ScenarioError(f"{where}: must be between {low:g} and {high:g}, got {value:g}")
    if value < low:
        raise ScenarioError(f"{where}: must be at least {low:g}, got {value:g}")
    return float(value)


def _integer(value: Any, where: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ScenarioError(f"{where}: expected an integer of at least {low}, got {value!r}")
    return value


def _seed(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScenarioError(f"{where}: expected a non-negative integer, got {value!r}")
    return value
