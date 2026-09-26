# Capacity-policy test harness

The harness replays a capacity policy on real post-RTC+B ERCOT market data and scores it. Project plan: [docs/handoff.md](docs/handoff.md).

```bash
make install        # .venv with the harness package and dev tools
make market-data    # build the ERCOT market dataset (see data/README.md)
make test           # offline tests from recorded ERCOT fixtures
make typecheck
```

## Run a policy

```bash
.venv/bin/harness run --scenario scenarios/baseline.yaml --start 2026-08-20 --end 2026-08-31
.venv/bin/harness run --policy constant_haircut --param fraction=0.8 \
    --scenario scenarios/stochastic.yaml --start 2026-03-08 --seed 3
.venv/bin/harness run \
    --policy "python examples/constant_haircut_policy.py --fraction 0.9" \
    --scenario scenarios/baseline.yaml --start 2026-03-08 --seed 3
```

The command prints a scorecard for each fleet case. It also writes two files to `data/runs/<scenario>_<policy>_<start>_<end>_seed<seed>/` (or `--out DIR`):

- `scorecard.json`, with one scorecard per fleet case. Each records `policy_view` and `observed_case`: the view that was used, and the fleet the policy saw.
- `intervals.parquet`, the per-interval data dump

`--end` defaults to `--start`, and `--seed` defaults to the scenario's `seed`. `--policy` is a built-in name or a shell-quoted external command. An external policy speaks JSON lines over stdin and stdout; see [docs/policy-protocol.md](docs/policy-protocol.md). Copy [examples/constant_haircut_policy.py](examples/constant_haircut_policy.py) to start one. `--decision-timeout` (default 1 second) and `--fallback` (`last_good` or `zero`) apply to that command. Run `harness run --help` for every option.

Python equivalent:

```python
from harness import ConstantHaircut, load_scenario, run

result = run(ConstantHaircut(fraction=0.9), load_scenario("scenarios/baseline.yaml"), start, end)
result.scorecards["P10"].totals["ECRS"].oversold_mw_h
```

### How a run works

- **One operating day is the unit of simulation.** A date-range run scores each CPT day on its own and combines the results. So it equals the combination of single-day runs (`Scorecard.combine`).
- **Fleet state has two modes** (`fleet.state`), both in `harness.fleet`. Each yields its own fleet cases, and each case gets its own scorecard. All cases are scored on the same market data.

  | Mode | Fleet cases | What varies |
  |---|---|---|
  | `quantile` (primary) | `P10`, `P25`, `P50`, `P75`, `P90` | A **quantile mock** gives the share of homes available, by CPT month and hour. Homes are ranked once per day, so a higher quantile always has more homes. Nothing fails mid-window. |
  | `stochastic` | `stochastic` | See the list below. |

  The stochastic model:
  - Homes **drop out**, losing telemetry and control.
  - Regions (the **failure domains**) have **grid outages**. Their homes go into backup mode and contribute zero.
  - Both happen at random with per-hour chances, and the chances are multiplied by `scarcity_stress` in scarce intervals.
  - Forced regional outages can be added at set times.
- **SOC** is fixed or drawn per home and day, and doesn't change within the day yet.
- **What the policy sees depends on the view.** It receives an **observation** and returns the MW it reports for each product (`{"ECRS": ..., "NONSPIN": ...}`).

  | `fleet.quantile_mock.policy_view` | Calls per interval | What the policy sees |
  |---|---|---|
  | `typical` (the default) | one | The `typical` quantile's fleet. That one K is scored against every quantile's true D. |
  | `per_case` | five, one per quantile | Each quantile's own fleet. |
  | stochastic mode | one, for the `stochastic` case | That case's own fleet. Stochastic mode always decides per case. |

  `typical` measures **planning uncertainty**: reality turns out worse or better than the typical fleet the policy planned for. Stochastic mode measures **operational uncertainty**: homes and regions drop out during the deployment window. In `per_case` the policy must not carry state from one quantile to the next; in `typical` there is only one call per interval.
- **The observation has six sections:**

  | Section | Contents |
  |---|---|
  | `now` | The interval's market row: start time (UTC and CPT), 5-minute RT MCPC and scarcity flag per product, RT price per load zone |
  | `fleet` | **Observed** state per region, at the interval's start. See the list below. |
  | `products` | Duration, pilot cap and cap share per product |
  | `history` | Empty for now |
  | `forecasts` | Empty for now |
  | `forecaster` | Empty for now |

  The `fleet` section has these fields per region:
  - `homes`
  - `homes_online`
  - `homes_stale`: telemetry older than `telemetry_stale_s`
  - `homes_backup`
  - `energy_above_floor_kwh` and `inverter_kw`, summed over the online homes
  - `capability_kw` per product: min(inverter kW, energy above floor ÷ duration), summed over the online homes

  Notes on the observation:
  - Stale and backup homes are excluded from the online totals.
  - A home that dropped out more recently than the stale threshold still looks online. That is the information gap between what a policy sees and what the fleet can do.
  - `harness.observation.observed_capability_mw` gives the fleet's observed MW for a product.
  - The observation holds only JSON values (missing data is `null`).
  - `now` is the interval's own row, so it includes that interval's prices and its scarcity flag. The flag's default threshold is a percentile over the whole dataset, which looks ahead (see [data/README.md](data/README.md#scarcity-proxy)).
- **Deliverable MW (D)** is the true MW the fleet could sustain if deployed. It counts only homes available for the product's whole duration from the interval's start: the sum of min(inverter kW, (SOC − floor) × kWh ÷ duration). A window that runs past midnight uses that day's draws.
- **Scoring**, with K the reported MW and an award min(MW, cap × cap share):
  - **Exposure, over every interval:**
    - over-sold = max(K − D, 0) and under-sold = max(D − K, 0), in MW-h;
    - overstatement rate = the share of intervals where K > D.
  - **Dollars, over intervals whose settlement price is `ok`:**
    - revenue = award of K × 15-minute RT settlement MCPC × 5/60 h.
    - Revenue given up is the regret against a hindsight oracle that reports exactly D: max(award of D − award of K, 0) × MCPC × 5/60 h. It is never negative. Shortfall costs come later.
  - **Unpriced intervals** are counted (`skipped`) and left out of the dollar sums.
- **The scorecard stores per-day sums and counts, never rates.** Rates are derived when shown. That way days combine exactly, and the run farm can split work by day. It also counts timeouts, malformed replies, restarts, and fallbacks from an external policy.
- **Randomness** comes from a seeded generator tree: one stream per (scenario, seed, day, stream name), in `harness.RandomStreams`. The streams are `soc`, `availability`, `home_dropouts` and `region_outages`. The policy isn't part of the key, so every policy faces the same draws.

### Scenario file

YAML. Every field is required, and unknown fields are errors, so a typo never silently falls back to a default. The scenario's name is its file name.

Every scenario carries both fleet-state models, the quantile mock and the failure model, and `fleet.state` picks which one drives the run. Switching modes is a one-line change.

[scenarios/baseline.yaml](scenarios/baseline.yaml) lists every field with its documented default: Base Core hardware, plus the handoff's placeholder fleet and failure rates, marked PLACEHOLDER. [scenarios/stochastic.yaml](scenarios/stochastic.yaml) is the same scenario with `state: stochastic`.

The handoff gives its calm and storm dropout chances per deployment window; here they are chances per hour.

| Field | Meaning |
|---|---|
| `seed` | Default random seed |
| `fleet.homes`, `fleet.regions` | Fleet size and failure domains; home *i* is in region *i* mod `regions` |
| `fleet.battery_kwh`, `fleet.inverter_kw`, `fleet.backup_floor` | Per home: 39.2 kWh, 20 kW and 20% for Base Core |
| `fleet.soc` | `{fixed: 0.6}`, or `{beta: [a, b]}` drawn per home and day |
| `fleet.telemetry_stale_s` | Telemetry older than this is stale (180 s) |
| `fleet.state` | `quantile` or `stochastic` |
| `fleet.quantile_mock.shares` | Used in quantile mode. Either flat shares `{P10: ..., P90: ...}`, or a CSV path (relative to the scenario) with columns `month,hour,P10,P25,P50,P75,P90`, one row per CPT month and hour. Shares may not fall from P10 to P90. |
| `fleet.quantile_mock.policy_view` | `typical` (default) or `per_case`. See the table above. |
| `fleet.quantile_mock.typical` | The quantile the policy sees in the `typical` view (`P50` by default). |
| `failures.home_dropout_per_h`, `failures.home_dropout_min` | Used in stochastic mode: the chance per hour that a home drops out, and how long |
| `failures.region_outage_per_h`, `failures.region_outage_min` | Used in stochastic mode: the chance per hour that a region's grid goes down, and how long |
| `failures.scarcity_stress` | Used in stochastic mode: both chances are multiplied by this in scarce intervals |
| `failures.forced_region_outages` | Used in stochastic mode: `[{region, start: "YYYY-MM-DD HH:MM" (CPT; add a UTC offset around DST changes), minutes}]` |
| `products.{ECRS,NONSPIN}.duration_h` | How long a deployment must be sustained: ECRS 1 h, Non-Spin 4 h |
| `products.{ECRS,NONSPIN}.cap_mw` | ADER pilot cap, system-wide MW |
| `products.{ECRS,NONSPIN}.cap_share` | Largest share of the cap one QSE may hold (0–1) |

### Data dump (`intervals.parquet`)

The dump has one row per 5-minute interval, fleet case and product. Columns:

- `interval_start_utc`, `interval_start_cpt`, `operating_day`
- `fleet_case`, `policy_view`, `observed_case`, `product`
- `reported_mw`, `deliverable_mw`, `award_mw`, `oversold_mw`, `undersold_mw`
- `rt_mcpc_5m`, `rt_mcpc_15m`, `scarce`
- `priced`, `revenue` and `revenue_given_up` (the last two are `NaN` when not priced)
- `lz_spp_{houston,north,south,west}`

Summing by fleet case and product gives the scorecard's totals:
- the dollar columns sum directly;
- the MW columns sum × 5/60 h.

## Components

- **Market dataset**: `harness.market`. ERCOT AS prices, load-zone prices, AS capability and demand curves on a 5-minute grid from Dec 5, 2025. It loads offline. See [data/README.md](data/README.md).
- **Harness core**: `harness`. It holds:
  - the scenario config (`scenario`);
  - the fleet-state model (`fleet`);
  - the policy interface and the constant-haircut policy (`policy`);
  - the observation (`observation`);
  - the run seam (`runner`);
  - the scorecard (`scorecard`);
  - the generator tree (`rng`);
  - the `harness` CLI (`cli`).
