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
.venv/bin/harness run --scenario scenarios/minimal.yaml --start 2026-08-20 --end 2026-08-31
.venv/bin/harness run --policy constant_haircut --param fraction=0.8 \
    --scenario scenarios/minimal.yaml --start 2026-03-08 --seed 3
.venv/bin/harness run \
    --policy "python examples/constant_haircut_policy.py --fraction 0.9 --nominal-mw 81" \
    --scenario scenarios/minimal.yaml --start 2026-03-08 --seed 3
```

The command prints a scorecard table. It also writes two files to `data/runs/<scenario>_<policy>_<start>_<end>_seed<seed>/` (or `--out DIR`):

- `scorecard.json`
- `intervals.parquet`, the per-interval data dump

`--end` defaults to `--start`, and `--seed` defaults to the scenario's `seed`. `--policy` is a built-in name or a shell-quoted external command. An external policy speaks JSON lines over stdin and stdout; see [docs/policy-protocol.md](docs/policy-protocol.md). Copy [examples/constant_haircut_policy.py](examples/constant_haircut_policy.py) to start one. `--decision-timeout` (default 1 second) and `--fallback` (`last_good` or `zero`) apply to that command. Run `harness run --help` for every option.

Python equivalent:

```python
from harness import ConstantHaircut, load_scenario, run

scenario = load_scenario("scenarios/minimal.yaml")
result = run(ConstantHaircut(fraction=0.9, nominal_mw=scenario.fleet.nominal_mw), scenario, start, end)
result.scorecard.totals["ECRS"].revenue
```

### How a run works

- **One operating day is the unit of simulation.** A date-range run scores each CPT day on its own and combines the results. So it equals the combination of single-day runs (`Scorecard.combine`).
- **The policy is called once per 5-minute interval.** It receives an **observation** and returns the MW it reports for each product (`{"ECRS": ..., "NONSPIN": ...}`).
- **The observation has six sections:**

  | Section | Contents |
  |---|---|
  | `now` | The interval's market row: start time (UTC and CPT), 5-minute RT MCPC and scarcity flag per product, RT price per load zone |
  | `products` | Pilot cap and cap share per product |
  | `history` | Empty for now |
  | `forecasts` | Empty for now |
  | `forecaster` | Empty for now |
  | `fleet` | Empty for now |

  - The observation holds only JSON values (missing data is `null`).
  - `now` is the interval's own row, so it includes that interval's prices and its scarcity flag. The flag's default threshold is a percentile over the whole dataset, which looks ahead (see [data/README.md](data/README.md#scarcity-proxy)).
- **Scoring** (reserve revenue only so far):
  - award = min(reported MW, cap × cap share);
  - revenue = award × 15-minute RT settlement MCPC × 5/60 h.

  An interval whose settlement price isn't `ok` in the dataset is skipped and counted, not scored.
- **The scorecard stores per-day sums and counts, never rates.** That way days combine exactly, and the run farm can split work by day. It also counts timeouts, malformed replies, restarts, and fallbacks.
- **Randomness** comes from a seeded generator tree: one stream per (scenario, seed, day, stream name), in `harness.RandomStreams`.
  - The policy isn't part of the key, so every policy faces the same draws.
  - Nothing draws from it yet. The failure and deployment models will.

### Scenario file

YAML. Every field is required, and unknown fields are errors, so a typo never silently falls back to a default. The scenario's name is its file name. See [scenarios/minimal.yaml](scenarios/minimal.yaml):

| Field | Meaning |
|---|---|
| `seed` | Default random seed |
| `fleet.nominal_mw` | MW the fleet can offer per product; the constant-haircut policy reports a fraction of it |
| `products.{ECRS,NONSPIN}.cap_mw` | ADER pilot cap, system-wide MW |
| `products.{ECRS,NONSPIN}.cap_share` | Largest share of the cap one QSE may hold (0–1) |

### Data dump (`intervals.parquet`)

The dump has one row per 5-minute interval and product. Columns:

- `interval_start_utc`, `interval_start_cpt`, `operating_day`
- `product`
- `reported_mw`, `award_mw`
- `rt_mcpc_5m`, `rt_mcpc_15m`, `scarce`
- `scored`, and `revenue` (`NaN` when not scored)
- `lz_spp_{houston,north,south,west}`

Summing `revenue` by product gives the scorecard's revenue.

## Components

- **Market dataset**: `harness.market`. ERCOT AS prices, load-zone prices, AS capability and demand curves on a 5-minute grid from Dec 5, 2025. It loads offline. See [data/README.md](data/README.md).
- **Harness core**: `harness`. It holds the scenario config (`scenario`), the policy interface and the constant-haircut policy (`policy`), the observation (`observation`), the run seam (`runner`), the scorecard (`scorecard`), the generator tree (`rng`) and the `harness` CLI (`cli`).
