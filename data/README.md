# ERCOT market dataset

This is the real market data the capacity-policy harness scores against. It covers every ERCOT operating day since **RTC+B went live on Dec 5, 2025**, on one **5-minute interval grid**. It is built once from ERCOT's public reports and stored locally. From then on it loads fully offline: scoring, backtests, the run farm and live replay read only this dataset and never call ERCOT.

```bash
make market-data          # build or extend: Dec 5, 2025 to yesterday; fetches only missing days
make market-report        # print the data-quality report
```

```python
from harness.market import load_intervals, load_asdc

df = load_intervals("2026-08-01", "2026-08-31")   # 5-minute table, indexed by interval_start_utc
curves = load_asdc("2026-09-01", "2026-09-07")    # hourly AS demand curves
```

- `load_intervals` raises `LookupError` if any day in the range hasn't been built.
- `python -m harness.market --help` lists every option: `--start/--end`, `--no-fetch` (offline re-normalize), `--refetch` and `--scarcity-percentile`.
- Set `HARNESS_DATA_DIR` to keep the data somewhere other than `data/`.

## Interval table (`load_intervals`)

There is one row per 5-minute interval of each CPT operating day: 288 on a normal day, **276** on the spring-forward day and **300** on the fall-back day. Column names are stable, and consumers depend only on this contract.

### Time columns

| Column | Meaning |
|---|---|
| `interval_start_utc` (index) | Interval start in UTC. Everything is keyed on this. |
| `interval_start_cpt` | The same instant in Central Prevailing Time (`America/Chicago`, tz-aware). |
| `operating_day` | ERCOT operating day (CPT date) the interval belongs to. |
| `cpt_dst` | `True` when Central Daylight Time (UTC−5) is in effect, `False` for CST (UTC−6). |
| `repeated_hour` | ERCOT's RepeatedHourFlag: `True` only on the second pass through 01:00–02:00 on the fall-back day. |

### Value columns

Units: **$/MW-h** for AS clearing prices (MCPC), **$/MWh** for energy prices (SPP), **MW** for capability. The products are Reg-Up (`regup`), Reg-Down (`regdn`), RRS (`rrs`), ECRS (`ecrs`) and Non-Spin (`nspin`).

| Column | Unit | ERCOT report | Meaning |
|---|---|---|---|
| `rt_mcpc_5m_{regup,regdn,rrs,ecrs,nspin}` | $/MW-h | NP6-332-CD | Real-time MCPC from the SCED run that started in the interval (capped MCPC). |
| `rt_mcpc_15m_{regup,regdn,rrs,ecrs,nspin}` | $/MW-h | NP6-331-CD | 15-minute **settlement** RT MCPC, including the RT reliability-deployment adder. Repeated on its three 5-minute rows. |
| `dam_mcpc_{regup,regdn,rrs,ecrs,nspin}` | $/MW-h | NP4-188-CD | Day-ahead MCPC. Repeated on the hour's twelve 5-minute rows. |
| `lz_spp_{houston,north,south,west}` | $/MWh | NP6-905-CD | Real-time settlement point price of `LZ_HOUSTON`, `LZ_NORTH`, `LZ_SOUTH`, `LZ_WEST` (type `LZ`, not the energy-weighted `LZEW`). Repeated on its three 5-minute rows. |
| `as_cap_{regup,regdn,rrs,ecrs,nspin}` | MW | NP6-328-CD | System-wide capability of resources available to provide each product, after SOC and duration limits. |
| `as_cap_regup_rrs`, `as_cap_regup_rrs_ecrs`, `as_cap_regup_rrs_ecrs_nspin` | MW | NP6-328-CD | Capability for combined products. These are not sums, because products share capacity. |
| `scarce_{regup,regdn,rrs,ecrs,nspin}` | bool | derived | Scarcity proxy, computed by the loader. See [Scarcity proxy](#scarcity-proxy). `<NA>` where the price is missing. |

### Quality flags

Every value column `<field>` has a flag column `q_<field>` (categorical):

| Flag | Meaning |
|---|---|
| `ok` | The value came from the source for this interval. |
| `gap` | The day's source file exists but has no value for this interval (for example, SCED didn't run). The value is `NaN`. |
| `no_source` | No source file exists for this day. The value is `NaN`. |

**Nothing is forward-filled.** Scoring should skip or mark intervals whose flags aren't `ok`.

## Demand-curve table (`load_asdc`)

This holds the AS demand curves (ASDCs, NP4-212-CD) as a separate hourly table, so the interval table stays small. Each row is one curve point: `interval_start_utc` (hour start), `interval_start_cpt`, `product`, `point`, `quantity_mw`, and `price` ($/MW-h). The table uses the **first** publication that carries a day, which ERCOT posts the day before, so it holds only what was known before the operating day. Days without a published curve have no rows.

## Sources and fetch routes

Each report is fetched per operating day into a raw cache. The routes below are tried in order until every requested day is covered. Days no route can cover are listed in the quality report.

| Data | Live report | 1. MIS history file (no key) | 2. MIS live documents (no key) | 3. ERCOT Public API (key) |
|---|---|---|---|---|
| RT MCPC per SCED run | NP6-332-CD | **NP6-795-ER** yearly workbook | about 7 days kept | archive, when credentials are set |
| RT MCPC, 15-min settlement | NP6-331-CD | **NP6-796-ER** yearly workbook | about 7 days | same |
| DAM MCPC | NP4-188-CD | **NP4-181-ER** yearly CSV | about 30 days | same |
| AS total capability | NP6-328-CD | **NP6-794-ER** yearly workbook | about 7 days | same |
| Load-zone and hub SPP | NP6-905-CD | **NP6-785-ER** yearly workbook | about 7 days | same |
| AS demand curves | NP4-212-CD | none | about 30 days | same |

We confirmed these routes on 2026-09-26:

- **The history files are yearly, not weekly.**
  - ERCOT posts one file per year on MIS and replaces it every week with the previous week added. The 2026 files posted on 2026-09-20 run through 2026-09-19.
  - Because of that lag, the last few days come from the live report's documents on MIS: one small CSV per SCED run or 15-minute interval.
  - All of this is public, so **the whole interval table can be built without an ERCOT API key**.
- **gridstatus's live methods are not used for fetching.**
  - `get_mcpc_sced`, `get_mcpc_real_time_15_min`, `get_as_prices`, `get_as_demand_curves_dam_and_sced` and `get_as_total_capability` read the same MIS documents, but only reach back a few days, and they don't keep the raw files.
  - The builder talks to MIS directly so raw files can be cached, and it uses the history files above.
  - gridstatus's `ErcotAPI` handles route 3.
- **ASDCs have no history file on MIS.**
  - Without a key, the dataset has demand curves only for the last ~30 days, counted from each build. Every build adds the newly available days, and the quality report lists the operating days that still have no curve.
  - With ERCOT API credentials, route 3 reads the Public API archive. Confirmed on 2026-09-26: the archive serves NP4-212-CD for every operating day from 2025-12-05. The route keeps the earliest publication of each day (usually the 04:55 CT post from the day before) and does not download later republishes.
- The 2025 NP6-795-ER file has a single `MCPC` column, which we read as capped MCPC. The 2026 file and the live report have both `CAPPED_MCPC` and `UNCAPPED_MCPC`. The dataset uses capped, which is the settlement price.
- NP4-181-ER's header has a stray space (`"REGUP "`). The parser strips header whitespace.

**Credentials.**

- Set `ERCOT_API_USERNAME`, `ERCOT_API_PASSWORD` and `ERCOT_PUBLIC_API_SUBSCRIPTION_KEY` in the environment to enable route 3.
- The builder only reads these variables. It never stores them and never creates accounts.
- Without them, the builder logs that it is using key-free routes only.

## How rows are aligned

- **Time zones.**
  - Source rows are local (CPT) wall-clock times.
  - They are placed on the UTC grid using ERCOT's own `RepeatedHourFlag`/`DSTFlag`. `Y` marks the second pass through the repeated fall-back hour.
  - ERCOT's hour-ending convention is used as is: HE01 is 00:00–01:00, and the spring-forward day has no HE03.
  - CPT offsets are whole hours, so UTC 15-minute and hour boundaries are also CPT boundaries.
- **SCED runs to 5-minute rows.**
  - A SCED run's price holds from its timestamp until the next run.
  - A row takes the run that **started** in its interval. When more than one run started there (about 1% of intervals in 2026), the row takes their time-weighted mean.
  - A row where no run started is flagged `gap`.
  - Time-weighting matches how ERCOT turns SCED prices into the 15-minute settlement price.
- **15-minute and hourly values** are copied onto exactly the three or twelve 5-minute rows they cover.

## Scarcity proxy

Public data doesn't show when ERCOT deployed ECRS or Non-Spin after RTC+B, so `scarce_<product>` is a documented proxy:

> An interval is scarce for a product when its **5-minute RT MCPC is above that product's 99th percentile** over every stored interval since Dec 5, 2025.

- **Configurable.**
  - Pass `PriceThresholdScarcity(percentile=..., price="rt_mcpc_15m", thresholds={"ecrs": 100.0})` as `scarcity=` to the loader, or use `--scarcity-percentile` on the CLI.
  - `thresholds` fixes the threshold for a product, in $/MW-h.
- **Swappable.** Any object with `flags(intervals, history) -> DataFrame` works (the `ScarcityProxy` protocol). `history(columns)` reads the chosen columns of every stored interval. The dataset stores the ingredients a better proxy needs: MCPC at both time steps, system capability per product, and the demand curves.
- **For the deployment model, not for policies.** The default threshold uses the whole history, so it looks ahead. It is meant as the harness's ground-truth signal. A policy should not see it at decision time.
- **It moves as the dataset grows.** The percentile is taken over the days that are stored, so extending the dataset can shift the threshold and change the `scarce_*` flags on existing rows. For runs you need to reproduce exactly, pass fixed `thresholds`.

## Storage

```
data/raw/<REPORT>/<YYYY-MM-DD>.csv.gz     raw rows, one ERCOT report and operating day, in the live report's CSV columns
data/raw/<REPORT>/<YYYY-MM-DD>.meta.json  which route fetched the file
data/raw/_downloads/                      cached history files (latest weekly version only)
data/market/intervals/<YYYY-MM-DD>.parquet
data/market/asdc/<YYYY-MM-DD>.parquet
data/market/quality_report.md             written after every build
```

Both `data/raw/` and `data/market/` are git-ignored.

- **Idempotent rebuilds.** A build rewrites each day's Parquet file from the raw cache, so rebuilding a range gives identical output.
- **Incremental builds.** A build fetches only the (report, day) raw files it doesn't already have. Use `--no-fetch` to re-normalize offline.
- **Test fixtures.** `tests/fixtures/raw/` holds a few recorded days in the same layout (see `scripts/record_fixtures.py`):
  - a normal day;
  - the spring-forward day, and the day after it (a contiguous two-day range for harness runs);
  - a price spike;
  - a day with a missing source file and a real SCED gap;
  - a **synthetic** fall-back day, because no fall-back has happened since RTC+B.
- `tests/fixtures/api/np4-212-cd.csv` is a recorded slice of the public-API archive (the 2025-12-05 04:56 CT document). Route 3's tests read it offline.

## Dataset summary

<!-- summary:start -->
From the build on 2026-09-26 (see `data/market/quality_report.md` for the full report):

- **Range:** 2025-12-05 to 2026-09-25. That is 295 operating days and 84,948 five-minute rows, including the 276-row spring-forward day.
- **Coverage:**
  - 15-minute settlement, day-ahead and load-zone prices are 100% covered.
  - 5-minute MCPC and AS capability are 99.99% covered. The 10 missing intervals are real SCED gaps, flagged `gap`.
  - Demand curves cover every operating day. 2025-12-05 to 2026-08-25 came from the ERCOT Public API archive; 2026-08-26 to 2026-09-25 came from key-free MIS.
- **Consistency check:** the average of each three 5-minute ECRS prices matches ERCOT's 15-minute settlement price to within $0.06/MW-h on average.
- **Scarcity (default proxy, 99th percentile of 5-minute RT MCPC):**

  | Product | Threshold ($/MW-h) | Scarce intervals |
  |---|---|---|
  | ECRS | 10.54 | 849 |
  | Non-Spin | 29.52 | 850 |
  | RRS | 7.18 | 849 |
  | Reg-Up | 12.17 | 849 |
  | Reg-Down | 10.00 | 634 |

- **Price ranges** (median / max):

  | Price | Median | Max |
  |---|---|---|
  | RT ECRS MCPC, 5-min | $0.05/MW-h | $821.83/MW-h (2026-08-26 22:20 CDT) |
  | RT Non-Spin MCPC, 5-min | $0.25/MW-h | $844.97/MW-h |
  | DAM ECRS MCPC | $0.39/MW-h | $1,000.65/MW-h |
  | Load-zone SPP | about $25/MWh | $1,612/MWh (North) |

<!-- summary:end -->

## Base-actual dataset (`load_base_actual`)

What Base's own home-battery fleet actually did in ERCOT, per SCED run. It comes from ERCOT's **60-Day SCED Disclosure** (NP3-965-ER), load-resource table. ERCOT publishes each operating day 60 days after it happens, so the newest day is always about two months old.

```bash
make base-actual            # ingest Dec 5, 2025 to the latest published day (about 55 MB download per day)
make base-actual-summary    # per resource per day stats, plus each dispatch-down event
```

```python
from harness.base_actual import BASE_QSE, daily_summary, dispatch_down_events, load_base_actual

rows = load_base_actual("2026-07-01", "2026-07-28", qse=BASE_QSE)  # or resources=["OB_ALD1"]
daily_summary(rows)                          # mean flexible MW, max awards, mean |deviation|, events
dispatch_down_events(rows, threshold_mw=5)   # one row per event
```

- `load_base_actual` raises `MarketDataMissing` for a day that hasn't been ingested.
- `python -m harness.base_actual build` reports days that aren't published yet (less than 60 days old) and days no MIS document could supply. Neither is an error.

**Rows.** There is one row per (SCED run, Aggregate Load Resource).

- The file has no resource-type column, so an ALR is a resource named `<SITE>_ALD<n>`, plus every resource under Base's QSE `QBASTX`.
- On 2026-07-20 that gave 13 ALRs across 8 QSEs. Base's are `OB_ALD1`, `SANSM_ALD1` and `MIDNT_ALD1`.
- Other QSEs' ALRs are kept for context.
- The raw cache keeps all 51 published columns of those rows.

| Column | Meaning |
|---|---|
| `sced_time_utc`, `sced_time_cpt` | SCED run time. Runs are not interval-aligned: about 00:00:20, 00:05:20, and so on. |
| `interval_start_utc` | The 5-minute market interval the run started in. This is the join key to `load_intervals`. |
| `operating_day`, `repeated_hour` | CPT operating day, and ERCOT's Repeated Hour Flag (the second 01:00–02:00 on fall-back). |
| `qse`, `dme`, `resource`, `status` | QSE, DME, resource name, and telemetered resource status (for example `ONL`). |
| `max_power_consumption_mw`, `low_power_consumption_mw`, `real_power_consumption_mw` | MPC, LPC, RPC (telemetered). |
| `base_point_mw` | SCED's instructed consumption. |
| `as_capability_ecrs_mw`, `as_capability_nspin_mw` | Telemetered AS capability. |
| `as_award_ecrs_mw`, `as_award_nspin_mw` | AS awards. An empty cell in the source means no award and is stored as 0. |
| `self_provided_ecrs_mw` | Self-provided ECRS. |
| `flexible_mw` | Derived: RPC − LPC, how far the resource could reduce consumption. |
| `deviation_mw` | Derived: RPC − Base Point. Positive means consuming more than SCED instructed. |

All quantities are MW.

**Dispatch-down events.**

- A SCED run is *dispatched down* when Base Point is more than a threshold below real power consumption (`deviation_mw > threshold`), meaning SCED asked the resource to consume less.
- An event is a run of consecutive dispatched-down SCED runs of one resource.
- The threshold is configurable (`threshold_mw=`, `--threshold`). It defaults to **2 MW**: on 2026-07-20, Base's mean |deviation| was 0.01–0.55 MW, while its one real dispatch moved it 6–25 MW.

**Sources.**

- MIS keeps every 60-day disclosure zip back to 2024, so every post-RTC+B day can be fetched without a key.
- ERCOT has reissued some days. For each operating day the builder takes, in order:
  1. the latest load-resource `…_SUPPLEMENTAL` zip whose report dates cover it (operating days 2025-12-05 to 2026-01-05 come from `60d_Load_Resource_Data_in_SCED_02032026_thru_03062026_SUPPLEMENTAL`);
  2. a `60_Day_SCED_Disclosure_CORRECTION` zip;
  3. the regular `60_Day_SCED_Disclosure` zip.
- The table is read raw. gridstatus 0.36.0's processed 60-day parser fails on post-RTC files ("Unknown curve type found").

**Storage.**

- `data/raw/NP3-965-ER/<day>.csv.gz`: ALR rows, all columns as published, about 70 KB per day.
- `data/market/base_actual/<day>.parquet`
- Rebuilds are idempotent, and only days missing from the raw cache are downloaded.

### Calibrated quantile mocks

A named mock set scenarios can select, scaled to any fleet size by `fleet.homes`. It keeps `scenarios/baseline.yaml`'s placeholder shares for comparison.

```bash
make calibrate-quantiles START=2026-07-01 END=2026-07-28
# optional: STORE=data/market  OUT=data/calibration/base-actual
```

```bash
python -m harness.calibration --store-dir data/market --start 2026-07-01 --end 2026-07-28 --out data/calibration/base-actual
```

The command reads the ingested store only. It does not download. Every operating day from `--start` through `--end` must already be in the store.

For each SCED run it sums flexible MW (RPC − LPC) across Base's ALRs (QSE `QBASTX`) and divides by the sum of headroom (MPC − LPC) on that same run. That share is flexible MW per MW of the fleet that reported. A run with no positive headroom is left out and counted in `skipped_runs`. Linear quantiles of the remaining shares, by CPT month and hour, are P10, P25, P50, P75 and P90. A quantile outside [0, 1] is clamped into that range afterwards, so an out-of-range run still moves the quantiles below it; `clipped_runs` counts those runs. A month and hour with no runs falls back to that hour's runs in other months, flagged `hour`. An hour that never occurs falls back to every run in the range, flagged `all`. The output is:

| File | What it is |
|---|---|
| `shares.csv` | Columns `month,hour,P10,P25,P50,P75,P90`. One row per month 1–12 and hour 0–23. Values in [0, 1], monotone. This is the share file `fleet.quantile_mock.shares` already loads. |
| `provenance.json` | `start`, `end`, `qse`, `resources`, `clipped_runs`, `skipped_runs`, and `fallback_buckets` (`month`, `hour`, `fallback`). |
| `bands.svg` | P10–P90 and P25–P75 bands, and the P50 line, at each hour of day that has runs. Hours with no runs are left blank. |
| `scenario.yaml` | The baseline fleet, failures and products, with `shares: shares.csv`. `harness run --scenario data/calibration/base-actual/scenario.yaml` scores against it. Edit `fleet.homes` to scale the profile. |

The output directory is git-ignored.

### Open questions

These need an answer, from Base or ERCOT, before relying on the numbers:

1. **The MW offset.** ADERs telemeter net load plus an ERCOT-assigned MW offset. So the absolute MPC, LPC, RPC and Base Point levels are not household consumption, and only differences (`flexible_mw`, `deviation_mw`) are meaningful. Each resource's offset is unknown.
2. **ECRS awards above telemetered ECRS capability.** On 2026-07-20, `OB_ALD1` held a 24.3 MW ECRS award in hours when its `AS Capability ECRS` was 10 MW (Non-Spin: 12.2 MW award against 30 MW capability). Either the capability column means something else for ALRs, or awards are made against a different limit.
3. **Mapping resources to Base's ADER groups.** Base publishes North, South and Houston groups. Which of `OB_ALD1`, `SANSM_ALD1` and `MIDNT_ALD1` is which isn't confirmed; the names look like sites, not zones.
4. **ALRs are identified by name** (`_ALD<n>`). The file has no resource-type column.
5. **What dispatch-down catches.** The rule only sees the lag between SCED's instruction and the resource's response. A resource that follows within one SCED run shows no deviation.
6. **Dispatch-down mostly follows energy prices, not AS deployments.** An ALR bids to buy energy, so SCED cuts its consumption when the load-zone price rises above its bid.
   - 2026-07-20 20:20: `OB_ALD1` and `SANSM_ALD1` were dispatched down while Houston's price hit $330/MWh. AS MCPCs stayed near $1–5 and the MCPC scarcity proxy didn't fire.
   - 2026-01-28: all three Base resources were dispatched down from 04:55 to about 08:00 CST. That began at about $350/MWh energy with low AS prices, and ran through the $399/MW-h ECRS spike.

   Separating an ECRS or Non-Spin deployment from an economic energy dispatch needs another signal (see #1 item C).

## Forecast-input store (`as_of`)

Every forecast a policy is allowed to see, keyed by **posted time** (when ERCOT published the vintage) and **valid time** (the hour or 5-minute interval it describes). Vintages are never merged: two postings of the same hour stay two rows. `as_of` is the only read path.

```bash
make forecast-data      # posted dates: 7 days ago through today; fetches only vintages not already cached
make forecast-report    # per input: posted-time range, vintage count, gaps
```

```python
from datetime import timedelta
from harness.forecast import as_of, build_forecasts

build_forecasts("2026-03-01", "2026-03-08")
as_of("2026-03-08T08:00:00Z", horizon=timedelta(hours=6))
# {"dam_as_plan": [{"posted_time": ..., "valid_time": ..., "series": "ECRS", "value": 100.0, "in_use": None}, ...]}
```

`as_of(T, inputs, horizon)` returns, per input, the rows of the **latest vintage with posted time ≤ T** whose valid time is in `[T, T + horizon)`. A row posted after T is never returned. Pass `inputs=["dam_as_plan", "load_by_model_zone"]` to read a subset. The horizon defaults to 168 hours.

`harness run --forecasts data/forecasts` fills each observation's `forecasts` from `as_of` at that interval's start. `--forecast-horizon` is the horizon in hours. Without `--forecasts` the section stays empty.

| Input | Report | What a series is | Unit |
|---|---|---|---|
| `load_by_model_zone` | NP3-565-CD | `{model}\|{zone}` (`coast`, `east`, `far_west`, `north`, `north_central`, `south_central`, `southern`, `west`, `system`). `in_use` says which model ERCOT is using. | MW |
| `wind_system` | NP4-732-CD | `stwpf` or `wgrpp`, then `system`, `lz_south_houston`, `lz_west`, `lz_north` | MW |
| `wind_region` | NP4-742-CD | `stwpf` or `wgrpp`, then `system`, `panhandle`, `coastal`, `south`, `west`, `north` | MW |
| `wind_by_model` | NP4-442-CD | `{model}\|{region}` (for example `S\|SYSTEM_TOTAL`). `in_use` marks the model copied into the other wind reports. | MW |
| `solar_system` | NP4-737-CD | `stppf` or `pvgrpp`, then `system` | MW |
| `solar_region` | NP4-745-CD | `stppf` or `pvgrpp`, then `system`, `center_west`, `north_west`, `far_west`, `far_east`, `south_east`, `center_east` | MW |
| `outage_capacity` | NP3-233-CD | `total_resource`, `total_irr` or `total_new_equip`, then `south`, `north`, `west`, `houston`. The file has no DST flag; the repeated fall-back hour is read as daylight time. | MW |
| `dam_as_plan` | NP4-33-CD | ancillary type, for example `ECRS`, `NSPIN` | MW |
| `dam_spp` | NP4-190-CD | load-zone and hub settlement points (`LZ_*`, `HB_*`). Resource nodes are not stored. | $/MWh |
| `dam_mcpc` | NP4-188-CD | ancillary type | $/MW-h |
| `rtd_lmp` | NP6-970-CD | load-zone and hub settlement points (types LZ, HU, SH, AH). Valid time is the start of the 5-minute interval. | $/MWh |
| `rtd_mcpc` | NP6-329-CD | `REGUP`, `REGDN`, `RRS`, `ECRS`, `NSPIN`. This report is not a filtered public-API endpoint; the archive is tried and a failure is a coverage-report error. | $/MW-h |

Posted time for every input except the two RTD reports' own run clocks is the document's publish time (MIS `PublishDate`, or the API `postDatetime`). A `postDatetime` with no UTC offset is Central time. One document is one vintage. Actual generation and COP HSL columns in the wind and solar files are not stored; those reports also carry realized output, and this store keeps the forecasts.

**Credentials and gaps.**

- Set `ERCOT_API_USERNAME`, `ERCOT_API_PASSWORD` and `ERCOT_PUBLIC_API_SUBSCRIPTION_KEY` to pull archive history by `postDatetime`. The builder only reads these variables.
- Without them, the build uses MIS. MIS keeps about a week of these reports. The coverage report lists, per input, the posted-time range that was stored, gaps longer than the report's cadence, and requested dates with no vintage.
- RTD runs about every 5 minutes, the hourly forecasts every hour, and the DAM reports about daily. A gap is a hole between successive postings, not a missing valid hour inside one vintage.

**Storage.**

```
data/forecast-raw/<REPORT>/<posted-time>__<doc-id>.csv.gz
data/forecast-raw/<REPORT>/<posted-time>__<doc-id>.meta.json
data/forecasts/<input>/<YYYY-MM-DD>.parquet    # posted date in Central Prevailing Time
data/forecasts/coverage.md
```

Both directories are git-ignored. A rebuild of a posted-date range rewrites the same rows. A vintage already in the raw cache is not downloaded again. `tests/fixtures/forecast/` holds a synthetic day in that raw layout, with several vintages of the same hours, so tests run with no network. The column layout matches MIS files read on 2026-09-26. The numbers are not a real ERCOT day.
