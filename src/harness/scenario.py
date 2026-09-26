"""Scenario config: every assumption a harness run depends on, in one YAML file.

    seed: 7
    fleet:
      nominal_mw: 81
    products:
      ECRS: {cap_mw: 100, cap_share: 0.9}
      NONSPIN: {cap_mw: 100, cap_share: 0.9}

Every field is required and unknown fields are rejected, so a typo never falls
back silently to a default. The scenario's name is its file name.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harness.products import PRODUCTS


class ScenarioError(ValueError):
    """An invalid scenario; the message names the offending field."""


@dataclass(frozen=True)
class ProductRules:
    cap_mw: float  # pilot cap, system-wide MW for the product
    cap_share: float  # largest share of the cap one QSE may hold

    @property
    def award_limit_mw(self) -> float:
        return self.cap_mw * self.cap_share


@dataclass(frozen=True)
class Fleet:
    nominal_mw: float  # MW the fleet can offer for each product


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int  # default seed for runs that don't pass one
    fleet: Fleet
    products: dict[str, ProductRules]


def load_scenario(path: Path | str) -> Scenario:
    """Read and validate a scenario YAML file. Raises ScenarioError."""
    path = Path(path)
    try:
        data = yaml.load(path.read_text(), Loader=_StrictLoader)
        return parse_scenario(data, name=path.stem)
    except OSError as e:
        raise ScenarioError(f"{path}: cannot read: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ScenarioError(f"{path}: not valid YAML: {e}") from e
    except ScenarioError as e:
        raise ScenarioError(f"{path}: {e}") from e


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


def parse_scenario(data: Any, name: str) -> Scenario:
    """Validate an already-parsed scenario mapping. Raises ScenarioError."""
    root = _exact_fields(data, "", ("seed", "fleet", "products"))
    fleet = _exact_fields(root["fleet"], "fleet", ("nominal_mw",))
    products = _exact_fields(root["products"], "products", tuple(PRODUCTS))
    return Scenario(
        name=name,
        seed=_seed(root["seed"], "seed"),
        fleet=Fleet(nominal_mw=_number(fleet["nominal_mw"], "fleet.nominal_mw", low=0)),
        products={p: _product_rules(products[p], f"products.{p}") for p in PRODUCTS},
    )


def _product_rules(value: Any, where: str) -> ProductRules:
    rules = _exact_fields(value, where, ("cap_mw", "cap_share"))
    return ProductRules(
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


def _seed(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScenarioError(f"{where}: expected a non-negative integer, got {value!r}")
    return value
