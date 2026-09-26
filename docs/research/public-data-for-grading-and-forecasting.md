# Public data for grading capacity algorithms and forecasting prices

_Researched 2026-09-26; §1a and §2 corrected from the #22 ingest. Method: read the gridstatus 0.36.0 source, the ERCOT public API spec bundled with it, and pulled one real 60-day SCED disclosure (operating day 2026-07-20). **[P]** means primary (ERCOT data or ERCOT API spec). **[G]** means the gridstatus source. **[O]** means our own pull and analysis. Anything marked "verify" is not yet confirmed._

## TL;DR

1. **Base's own real-time reserve activity is public, with a 60-day delay.** The 60-Day SCED Disclosure (NP3-965-ER) has a load-resource file. It lists QSE **`QBASTX`** (Base Texas QSE) with three Aggregate Load Resources: **`OB_ALD1`, `SANSM_ALD1`, `MIDNT_ALD1`**. For every SCED interval it gives:
   - telemetered Max/Low Power Consumption;
   - Real Power Consumption;
   - Base Point;
   - AS Capability for ECRS and Non-Spin;
   - AS Awards for ECRS and Non-Spin.

   This gives the harness three things it had to assume before:
   - **What Base actually did.** We can replay it as a baseline "policy".
   - **How well Base delivered.** Real consumption vs. base point.
   - **Calibration for the fleet-state mocks.** Quantiles of flexible MW by hour and season. [P][O]
2. **Every price we grade against is public at 5-minute or 15-minute resolution since RTC+B.** That means RT MCPC per product and RT load-zone prices, with history in the ERCOT archives. [P][G]
3. **Most forecast inputs are published with a posting time**, so the harness can rebuild *what was known at decision time* with no look-ahead. The ERCOT public API exposes a `postDatetime` filter on:
   - load forecasts;
   - wind and solar forecasts;
   - outage capacity;
   - the DAM AS plan. [P]

   The DAM prices published the day before are a ready-made forecast of real-time prices.
4. **Deployment events are the weakest link.** No single public "ERCOT deployed ECRS at time t" feed was found. The best options:
   - base-point drops on ADER load resources (from item 1). These mostly track energy prices, not AS deployments (see §2), so on their own they are not a deployment detector;
   - RT reserves and reliability-deployment adders (NP6-323-CD);
   - control-room operations messages;
   - RT MCPC spikes as a proxy.

## 1. Grading data: did an algorithm over-sell or under-sell?

For each historical interval, an algorithm outputs capability K per product. We grade K against three references.

| Reference | What it measures | Source |
|---|---|---|
| **Base actual** | Difference from what Base really reported and was awarded | 60-Day SCED load-resource file, QSE `QBASTX` [P][O] |
| **Deliverable MW (D) under fleet-state mocks** (P10…P90) | Over-sell = max(K − D, 0); under-sell = max(D − K, 0) | Mocks calibrated from Base's public telemetry (below) |
| **Realized prices** | Revenue = award × RT MCPC; opportunity cost of under-selling = under-sell × RT MCPC; energy price of any shortfall | NP6-331-CD (15-min MCPC), NP6-332-CD (5-min), NP6-905-CD (load-zone SPP); history via the archive (NP6-795-ER for MCPC) [P][G] |

### 1a. Base's aggregate load resources in the 60-day SCED disclosure [P][O]

- **Report:** NP3-965-ER, "60-Day Load Resource Data in SCED". It's published 60 days after the operating day, and the public API also serves an archive of it.
- **Fields per SCED interval** (raw file, post-RTC column names):
  - `SCED Time Stamp` (gridstatus renames it `SCED Timestamp`), `QSE`, `DME`, `Resource Name`, `Telemetered Resource Status`;
  - `Max Power Consumption`, `Low Power Consumption`, `Real Power Consumption`, `Base Point`;
  - `AS Capability NSPIN`, `AS Capability ECRS`;
  - `AS Awards NSPIN`, `AS Awards ECRS` (and other products);
  - `Self Provided ECRS`;
  - ramp rates and bid-to-buy curves.
- **One-day look (2026-07-20):**

  | Resource | Avg flexible MW (RPC − LPC) | Max ECRS award | Max Non-Spin award | Mean abs(RPC − Base Point) | Max deviation |
  |---|---|---|---|---|---|
  | OB_ALD1 | 47.6 | 24.3 | 12.2 | 0.55 | 25.0 |
  | SANSM_ALD1 | 21.4 | 21.0 (held in 124 SCED runs) | 7.0 | 0.14 | 6.1 |
  | MIDNT_ALD1 | 6.55 | 6.4 (held in all 288 runs) | — | 0.01 | 0.3 |

  - On OB_ALD1, awards switch from hour to hour between about 24.3 MW of ECRS and about 12.2 MW of Non-Spin.
  - Telemetered AS Capability sits flat at 10 (ECRS) and 30 (Non-Spin).
- **Things to verify before relying on these fields:**
  - MPC and RPC include the ERCOT-assigned **MW offset** that ADERs must telemeter as net load, so only differences (RPC − LPC, RPC − Base Point) are meaningful.
  - An ECRS award (24.3) above the telemetered ECRS capability (10) needs explaining. The capability field may mean something else for ALRs, or awards may be set elsewhere.
  - The 25 MW deviation on OB_ALD1 may be a deployment, a ramp, or a telemetry artefact.
  - The resource-name-to-ADER-group mapping (Base publishes North, South, Houston) is not confirmed.
  - One day is not a sample. Pull a few weeks, including scarcity days.
- **Tooling:** gridstatus's processed 60-day SCED parser raised "Unknown curve type found" on the post-RTC files. Read the raw tables instead (`process=False`). [O]

### 1b. How this data feeds the harness
- **"Base actual" baseline:** replay Base's historical awards as a policy, so every algorithm is also scored as "vs. what Base did".
- **Delivery grading:** in intervals where Base Point drops below consumption (dispatch down), measure how closely RPC follows. That's a real under-delivery signal for the pilot's largest participant. Most of these are **energy dispatch**, not reserve deployments (see §2).
- **Fleet-state mocks:** build P10/P25/P50/P75/P90 profiles of flexible MW (RPC − LPC) by hour of day and month from Base's own telemetry, scaled to any fleet size. The mocks come from real data, not invented numbers.

## 2. Deployment and scarcity signals

| Signal | What it gives | Historical access | Notes |
|---|---|---|---|
| Base-point drops on ADER load resources | Direct evidence of dispatch-down for Base's resources | 60-day lag, NP3-965-ER [P][O] | Good for grading delivery. **Mostly energy-driven, not AS deployments** (see the note below), so not a deployment detector on its own |
| RT ORDC / reliability deployment price adders and reserves by SCED interval | System reserve levels and deployment adders | MIS keeps about 5 days [G]; the archive has older data (verify fields after RTC+B) | NP6-323-CD |
| Control-room operations messages | Explicit deployment and EEA notices | Live page keeps about a month; older via Wayback snapshots [G] | Patchy but explicit |
| RT MCPC spikes | Scarcity proxy | Full, from Dec 5, 2025 | Our current default proxy |
| AS capacity monitor, real-time system conditions | Live reserves | "latest" only [G] | Can be recorded going forward, not backtested |

**Dispatch-down is mostly energy-driven** [O, from the #22 ingest]. Two examples:
- On 2026-07-20 at 20:20, OB_ALD1 and SANSM_ALD1 were dispatched down while the Houston SPP was about $330/MWh and ECRS MCPC was about $1.4.
- On 2026-01-28, all three Base resources were dispatched down from 04:55 to about 08:00 CST. It started at about $350/MWh energy with low AS prices, then ran through a $399 ECRS spike.

So a base-point drop alone doesn't identify an ECRS or Non-Spin deployment. It has to be combined with AS price, reserve and control-room signals. This is open question 6 in data/README.md.

## 3. Forecast inputs, point-in-time

A forecaster may only use data **posted before** the decision time. These ERCOT public API endpoints support a `postDatetime` filter, which is what makes point-in-time replay possible. [P, from the pubapi spec bundled in gridstatus]

| Input | Report | Horizon / cadence |
|---|---|---|
| Seven-day load forecast by model and weather zone | NP3-565-CD (also NP3-566-CD by study area) | Hourly, 7 days ahead, posted hourly |
| Wind forecast, system-wide, regional and by model (STWPF/WGRPP) | NP4-732-CD, NP4-742-CD, NP4-442-CD | Hourly, 168 h ahead, posted hourly |
| Solar forecast, system-wide and regional (STPPF/PVGRPP) | NP4-737-CD, NP4-745-CD | Hourly, 168 h ahead |
| Hourly resource outage capacity (a proxy for thermal outages) | NP3-233-CD | 7 days ahead, posted hourly |
| DAM AS plan (AS requirements) | NP4-33-CD | Hourly, today plus 6 days |
| Load distribution factors | NP4-159-CD | — |

These don't have a `postDatetime` filter, but their publication schedule is known:

| Input | Report | How it's used |
|---|---|---|
| DAM settlement point prices and DAM MCPC | NP4-190-CD, NP4-188-CD | Posted the afternoon before the operating day. A strong baseline forecast of RT. |
| RTD indicative LMPs and MCPC | NP6-970-CD, NP6-329-CD | About an hour ahead at 5-minute steps. The best short-horizon forecast. |
| Temperature forecast by weather zone | via gridstatus | Posted daily at about 5 am CT [G] |
| Short-term system adequacy | via gridstatus | Posted repeatedly; verify archive depth |

Past realized values (prices, load, wind, solar actuals) are always usable once they're posted.

**Weather beyond ERCOT's own forecasts** (for example NOAA alerts for storms) is optional and not researched here.

## 4. Grading a price forecaster
- **Output:** a price trajectory for the next N hours, covering the RT load-zone price and RT MCPC per product, as quantiles.
- **Accuracy metrics:** MAE and RMSE on the median; pinball loss per quantile; calibration (share of outcomes inside each quantile band); **spike skill** (precision and recall on the top-x% price intervals).
- **Value to decisions:** feed the forecast into a capacity algorithm and compare its capacity score with a perfect-foresight forecast and with a naive forecast. That's the value of the forecast in dollars and reliability terms, which is what Base cares about.
- **Reference forecasters:**
  - persistence;
  - DAM-as-forecast;
  - RTD-indicative for the next hour;
  - a simple regression on net-load forecast (load − wind − solar) and outage capacity.

## 5. Access and tooling notes
- **gridstatus `Ercot()`** (MIS scraping) needs no key, but many reports keep only a few days. Examples: RT adders and reserves (about 5 days), RT MCPC documents. [G]
- **gridstatus `ErcotAPI()` / ERCOT public API** needs the user's API credentials (username, password, subscription key). It gives archive access (`/archive/{emilId}/download`) and posting-time filters. gridstatus switches to the historical API for dates more than about 90 days back. [G][P]
- **The bundled API spec** did not list NP6-331/332 (RT MCPC) as filterable endpoints. Get them through the archive route. [P]
- **60-day disclosure files are large** (about 150k load-resource rows per day). Store only the rows and columns needed, for example the ADER resources and system totals.

## Sources
- gridstatus 0.36.0: `gridstatus/ercot.py`, `ercot_60d_utils.py`, `ercot_api/ercot_api.py`, and the bundled `ercot_api/pubapi-apim-api.json`. https://github.com/gridstatus/gridstatus
- ERCOT data product pages: https://www.ercot.com/mp/data-products/data-product-details?id=NP3-965-ER · NP6-331-CD · NP6-332-CD · NP6-795-ER · NP6-905-CD · NP6-323-CD · NP3-565-CD · NP4-732-CD · NP4-737-CD · NP3-233-CD · NP4-33-CD · NP4-188-CD · NP4-190-CD · NP6-970-CD · NP6-329-CD
- ERCOT data product archive: https://data.ercot.com/data-product-archive/{EMIL ID}
- Our pull: `Ercot().get_60_day_sced_disclosure(date="2026-07-20", process=False)`, load-resource table filtered to `QSE == "QBASTX"`.
