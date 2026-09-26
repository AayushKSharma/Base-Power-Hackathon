"""Scarcity proxies: which intervals count as reserve-scarce, per AS product.

Public data does not say when ERCOT deployed ECRS or Non-Spin after RTC+B, so
the dataset flags scarcity with a documented, swappable proxy. The loader calls
`proxy.flags(intervals, history)` and stores the result as `scarce_<product>`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from harness.market.catalog import PRODUCTS

# Reads the given columns of every stored interval (all days since RTC+B).
History = Callable[[list[str]], pd.DataFrame]


class ScarcityProxy(Protocol):
    def flags(self, intervals: pd.DataFrame, history: History) -> pd.DataFrame:
        """One boolean column per product suffix ("ecrs", "nspin", ...), indexed
        like `intervals`. NA where the proxy cannot tell."""
        ...


@dataclass(frozen=True)
class PriceThresholdScarcity:
    """Scarce when the product's real-time MCPC is above a threshold.

    The threshold is `thresholds[product]` when given, otherwise the
    `percentile` of that product's price over all stored intervals since
    RTC+B (Dec 5, 2025). The flag is NA where the price is missing.
    """

    percentile: float = 0.99
    price: str = "rt_mcpc_5m"  # or "rt_mcpc_15m" for the settlement price
    thresholds: Mapping[str, float] = field(default_factory=dict)

    def threshold_values(self, history: History) -> dict[str, float]:
        out = {p: float(t) for p, t in self.thresholds.items()}
        todo = [p for p in PRODUCTS.values() if p not in out]
        if todo:
            prices = history([f"{self.price}_{p}" for p in todo])
            for p in todo:
                out[p] = float(prices[f"{self.price}_{p}"].quantile(self.percentile))
        return out

    def flags(self, intervals: pd.DataFrame, history: History) -> pd.DataFrame:
        out = {}
        for product, threshold in self.threshold_values(history).items():
            price = intervals[f"{self.price}_{product}"]
            out[product] = (price > threshold).astype("boolean").mask(price.isna())
        return pd.DataFrame(out, index=intervals.index)


DEFAULT_SCARCITY = PriceThresholdScarcity()
