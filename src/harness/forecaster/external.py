"""A price forecaster in another process, on the same session as an external policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from harness.external import JsonChild
from harness.forecaster.grade import zero_forecast
from harness.protocol import for_policy, forecast_message, parse_forecast
from harness.scenario import ProductRules
from harness.scorecard import FaultCounts


class ExternalForecaster:
    """`forecast` is one `forecast` message. Timeouts, crashes and bad replies
    use the policy fallback and are counted on `faults`.
    """

    def __init__(self, command: Sequence[str], products: Mapping[str, ProductRules] | None = None, *,
                 timeout_s: float = 1.0, fallback: str = "last_good"):
        self._child = JsonChild(command, {} if products is None else products,
                                timeout_s=timeout_s, fallback=fallback)

    @property
    def name(self) -> str:
        return self._child.name

    @property
    def look_ahead(self) -> bool:
        return self._child.look_ahead

    @property
    def faults(self) -> FaultCounts:
        return self._child.faults

    def begin_day(self) -> None:
        self._child.begin_day()

    def forecast(self, observation: Mapping[str, Any], *, horizon_hours: int,
                 quantiles: Sequence[float]) -> dict[str, Any]:
        issued = str(observation["now"]["interval_start_utc"])

        def parse(message: Any) -> dict[str, Any] | None:
            return parse_forecast(message, issued_at=issued, horizon_hours=horizon_hours, quantiles=quantiles)

        reply = self._child.request(
            lambda wants: forecast_message(for_policy(observation, wants_per_home=wants),
                                           horizon_hours=horizon_hours, quantiles=quantiles),
            parse,
            lambda: zero_forecast(issued, horizon_hours, quantiles),
        )
        # A last-good fallback still carries the previous decision's timestamp.
        if reply.get("issued_at") != issued:
            reply = dict(reply)
            reply["issued_at"] = issued
        return reply

    def close(self) -> None:
        self._child.close()

    def __enter__(self) -> ExternalForecaster:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
