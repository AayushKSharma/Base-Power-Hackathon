# Policy protocol

Version 1. An external policy is a process the harness starts. The harness writes JSON to the policy's stdin and reads JSON from its stdout. Each message is one UTF-8 JSON object followed by a newline. The policy must flush every line. stderr is not part of the protocol; the harness drains it so a chatty process cannot block, and keeps the tail for error messages.

`harness run --policy` takes a built-in name, or a command parsed with shell quoting. `--param` applies only to built-in policies.

```bash
harness run \
    --policy "python examples/constant_haircut_policy.py --fraction 0.9" \
    --scenario scenarios/baseline.yaml --start 2026-03-08
```

[`examples/constant_haircut_policy.py`](../examples/constant_haircut_policy.py) is the template.

## Versioning

The harness sends `protocol_version` on `hello`. This document is version 1. A policy ignores fields it does not know. The reply's `version` is the policy's own version string, not the protocol version. A policy that exits because it cannot speak this version is a crash for that decision: the harness counts a restart and a fallback, and continues.

## Messages

### hello

The harness sends this first, and again after a restart or a new operating day.

```json
{
  "type": "hello",
  "protocol_version": 1,
  "products": {
    "ECRS": {"duration_h": 1.0, "cap_mw": 100.0, "cap_share": 0.9},
    "NONSPIN": {"duration_h": 4.0, "cap_mw": 100.0, "cap_share": 0.9}
  }
}
```

`products` are the scenario's product rules. The set of fields grows with the scenario. The reply:

```json
{"name": "constant_haircut(fraction=0.9)", "version": "1", "wants_per_home": false}
```

| Field | Rule |
|---|---|
| `name` | Non-empty string. This is the scorecard label. |
| `version` | String. The policy's version. |
| `wants_per_home` | Boolean. `true` or `false` only, not `0` or `1`. |

Anything else is a malformed hello. The harness counts `malformed` and `fallbacks`, uses the fallback for that decision, kills the process, and continues. The next decision starts a new process. That kill is not a restart: the process did not crash. A command that cannot be started at all still stops the run.

### observation

One per interval, after the handshake.

```json
{"type": "observation", "observation": { }}
```

`observation` is the object the harness built, unchanged: `now`, `history`, `forecasts`, `forecaster`, `fleet`, and `products`. It may also carry per-home state. The harness removes that state unless `wants_per_home` is true.

Per-home state is the top-level `homes` list, or `fleet["homes"]` when a fleet model stores it there. A home record, when supplied in the shape from the plug-in spec, is:

```json
{"home_id": "h1", "region_id": "houston", "soc": 0.5, "last_seen_s": 12, "backup_mode": false}
```

The fleet model does not produce homes yet. `run(..., homes=[...])` attaches the same list to every observation so the gate can be tested.

### decision

The policy answers each observation with:

```json
{"capability_mw": {"ECRS": 72.9, "NONSPIN": 72.9}}
```

Both products are required. Extra fields are ignored. A number above the fleet's nominal capability is allowed: overstatement is what the scorecard measures.

### forecast

A price forecaster speaks the same hello, then answers a forecast request instead of an observation. The harness may also send `look_ahead` on the hello reply; only `true` marks a look-ahead forecaster, and anything else counts as false.

```json
{"type": "forecast", "horizon_hours": 24, "quantiles": [0.1, 0.5, 0.9], "observation": {}}
```

`observation` is the point-in-time view: realized rows in `history` start strictly before the decision, and `forecasts` is the forecast store's `as_of` at that time. The reply:

```json
{
  "issued_at": "2026-03-08T06:00:00+00:00",
  "horizon_hours": 24,
  "series": {
    "LZ_HOUSTON": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]},
    "LZ_NORTH": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]},
    "LZ_SOUTH": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]},
    "LZ_WEST": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]},
    "MCPC_ECRS": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]},
    "MCPC_NSPIN": {"quantiles": [0.1, 0.5, 0.9], "values": [[], [], []]}
  }
}
```

The six series are required. `values[i]` is the trajectory of `quantiles[i]`, one finite number per hour, starting at `issued_at`. The first hour may instead be twelve 5-minute steps: each trajectory then has `12 + horizon_hours - 1` numbers, the first twelve covering that hour and the rest hourly. A missing series, a non-finite number, or the wrong length is malformed. The timeout, fallback and restart rules below apply unchanged. [`examples/persistence_forecaster.py`](../examples/persistence_forecaster.py) is the template.

## Malformed decisions

A decision is malformed when any of these is true:

- the line is not JSON, or the JSON is not an object;
- `capability_mw` is missing or not an object;
- `ECRS` or `NONSPIN` is missing, null, a boolean, a string, or any other non-number;
- a value is negative, or not finite.

The harness counts `malformed` and `fallbacks`, reports the fallback capability, and keeps the process. The bad line is already consumed, so the next observation stays in step. The run completes.

## Time budget

Each reply, including the hello reply, has a wall-clock budget. The default is 1 second. `ExternalPolicy(..., timeout_s=)` and `harness run --decision-timeout` set it.

On timeout the harness kills the process, so a late line cannot be read as the next decision. It counts `timeouts` and `fallbacks`. The kill is not a restart. The next decision starts a new process.

## Fallback

`--fallback` is `last_good` (the default) or `zero`.

- `last_good` repeats the last well-formed capability of this operating day. Before any well-formed reply, it is zero for every product.
- `zero` reports 0 MW for every product.

A timeout, crash, or malformed decision uses the fallback and counts one `fallbacks`. The scorecard stores the four counts per day and prints the sums. Days still store counts, never rates, so they combine exactly.

## Crashes and operating days

If the process exits or closes stdout before a well-formed reply, the harness counts one `restarts` and one `fallbacks` for that decision, and starts a new process on the next decision. The run completes.

The harness also replaces the process at each operating-day boundary and forgets `last_good`. That replacement is not a restart. In the `per_case` view it does the same before each fleet case, so a fallback cannot leak from one quantile to the next. A date-range run therefore stays equal to the combination of its single-day runs, for a policy that does not smuggle state anywhere except its own memory. In the `typical` view there is one decision pass per day, and every fleet case records that pass's fault counts. Those copies are the same events.
