# Demo script and run guide

Loom, camera on, one take, under 5 minutes. The spoken script below is the required order: team, elevator pitch, live demo, how it was built (said while the demo is on screen), then who it is for and what is next.

Git history has one author, Aayush Kumar Sharma. The intro is written for that. If someone else is on camera, they replace the second sentence with their name and the piece they built, and the whole intro still ends before 30 seconds.

This is a test bench for the capability number Base already reports. The built-in policies exist so the bench can tell policies apart. They are not a bid to replace the desk. Open Grid Data is the real post-RTC+B tape. Orchestration is an untrusted policy process, stale telemetry, and a killed coordinator that still produce a score.

## Already on disk

Do not re-run these during the recording. Re-running replaces the published files.

| Show this | Path | Why it is already done |
|---|---|---|
| Headline comparison | `docs/insights/compare-findings.md` and `docs/insights/compare/charts/` | `make backtest` rewrites `docs/insights/compare/`. The published week is 11–17 Aug 2026, seed 7. |
| Farm benchmark | `docs/bench/bench.md` | `make bench` replaces that file with this machine's times. The recorded table is the one to read. |
| Live scale | `docs/bench/live.md` | 10k and 50k agent tick rates. `harness bench-live` rewrites the file. |
| Assumptions | `README.md` scenario table, `docs/design-notes.md` | The spoken assumptions section follows those. |
| Market tape | `data/market` | 5 Dec 2025 through 25 Sep 2026, 295 operating days. Built. |
| Postgres | `127.0.0.1:54329`, user and database `harness` | Already up. Compose file is `compose.yaml`. |

`make market-data` is not needed unless `data/market/intervals` is missing. It only fetches days that are missing.

## What to run, in order

From the repo root. The market dataset is local. Nothing in this list needs the ERCOT API.

1. Confirm the tape, about a second.

   ```bash
   make market-report
   ```

   Expect operating days 2025-12-05 through 2026-09-25. 15-minute RT MCPC and load-zone prices are fully covered. Five-minute MCPC is 99.99%.

2. Open the published comparison. Do not regenerate it.

   - `docs/insights/compare/charts/frontier.png`
   - `docs/insights/compare/charts/rankings.png`
   - `docs/insights/compare/comparison.md` (the P10 ECRS tolerance tables)
   - `docs/insights/compare-findings.md` (the sentences you will say)

3. Live replay, already run once. Re-run only if you want it on camera. About 70 seconds for the plain run, about 90 seconds with chaos. Both write 150 ticks, which is 5 simulated minutes at a 2-second tick.

   ```bash
   make replay OUT=data/replay/plain
   make chaos  OUT=data/replay/chaos
   ```

   `make chaos` starts Postgres only if the port is down. Use two output directories. The default of both targets is `data/replay`, and the second call overwrites the first.

   Last run, 17 Aug 2026, seed 7, constant 90% haircut:

   | Run | Wall | Ticks | Reaction | Recovery | Floor violations |
   |---|---|---|---|---|---|
   | plain | 67 s | 150 | n/a (no failures) | n/a | 0 |
   | chaos | 91 s | 150 | p50 and p99 both 180.000 s | p50 and p99 both 0.000 s | 0 |

   Chaos also printed duplicate commands 0 and commands to dead agents 0. Chart: `data/replay/chaos/timeline.png`.

4. Keep `docs/bench/bench.md` on screen for the farm beat. Do not run `make bench` unless you want new numbers in the repo.

### Optional, not in the five minutes

- **Forecast value.** `data/forecasts` is not built. Persistence and the oracle can still be graded from the market tape, but it is a second movie:

  ```bash
  .venv/bin/harness value --policy independent_newsvendor --forecaster persistence \
      --scenario baseline --start 2026-08-17 --out data/value
  ```

- **Base's own awards as a policy.** `base_actual` reads QSE `QBASTX` from `data/market/base_actual`. That store has 61 days starting 2025-12-05. It does not cover 11–17 Aug 2026. Do not run it against the headline week.

- **Quantile calibration.** Needs a start and end inside the ingested Base-actual days:

  ```bash
  make calibrate-quantiles START=2025-12-05 END=2026-01-31
  ```

  Writes `data/calibration/base-actual`. The baseline scenario still uses the flat placeholder shares.

## What each screen is

**Frontier** (`frontier.png`). Three panels, baseline, caps lifted, Non-Spin at 2 hours. Vertical axis is shortfall MW-h. Every policy sits on zero. Horizontal axis is revenue. The independent newsvendor is furthest right. The reliability target at ε = 0.01 is furthest left. A tighter ε on this week buys no reliability. It only gives up revenue.

**Rankings** (`rankings.png`). Net dollars. Blue and orange (baseline and caps lifted) stay in the same order. Green (Non-Spin at 2 h) is much higher for everyone, and the correlated newsvendor moves past the haircut. Say that one flip. Do not say the whole ranking reshuffles.

**P10 tolerance table** in `comparison.md`. On baseline P10 ECRS, P(an hour under-serves by at least 1 MW) is 0% for the haircut and for ε = 0.01, 3.6% for the correlated newsvendor, and about 16% for the independent newsvendor and for ε of 0.05 and above. It is 0% at 5 MW for all of them. The policy never saw the P10 fleet. It saw P50. Those rows are what happens if fewer homes were actually there.

**Chaos chart.** Simulated seconds 0 to 300. Four series. On this window, commanded and delivered sit on zero: no deployment was drawn, so the dispatch loop is idle. The two lines that move are reported capability and true deliverable.

| Simulated time | What happened | What the lines do |
|---|---|---|
| 0 | 90% haircut of a healthy fleet | Reported 15.685 MW, true deliverable 17.428 MW. Ratio is 0.90. |
| 2 | Agent hosts for regions 0 and 1 are killed | Neither line moves. |
| 30–50 | 10% of messages dropped | No MW change. |
| 60–80 | Telemetry delayed 4 seconds | No MW change. |
| 90–120 | Region 2 partitioned | No separate MW step. |
| 100 | Region 3 in outage, those homes in backup | Both lines drop, and the ratio stays 0.90. Reported 14.113, deliverable 15.681. The outage is visible at once. |
| 182 | 180 seconds after the host kills | Only the reported line drops, to 11.040 MW. Deliverable stays 15.681. Telemetry older than 180 seconds is stale, so the report stops counting those hosts. The batteries behind a dead agent process are still in true deliverable. |
| 200 | Coordinator restart from Postgres | The MW lines do not move. Floor violations stay 0. No duplicate commands, no commands to dead agents. |

Reaction p50 and p99 are both 180 seconds because that is the stale rule, not a measured network delay. Recovery reads 0 because delivered MW was already on the commanded award, and the award was zero. Do not describe 0 as instant recovery of a discharge.

## Before you hit record

Camera on, Loom, one take. Pre-type this in a terminal and do not press Enter until the pitch ends. It prints in about 11 seconds:

```bash
.venv/bin/harness run --policy constant_haircut --param fraction=0.9 \
    --scenario baseline --start 2026-08-17 --out data/replay/oneday
```

Have these open behind the terminal, in this order: `docs/insights/compare/charts/rankings.png`, then `data/replay/chaos/timeline.png`. Do not open the frontier chart. It is a flat line at zero shortfall, and the console table already shows that the shortfall is on P10.

Do not run `make backtest`, `make bench`, or `make chaos` on camera. The week and the farm rewrite published files. Chaos takes about 90 seconds, which would blow the five minutes. You already have its log: reaction 180 seconds, recovery 0, floor violations 0.

## Script

Read the times as a ceiling. Sections 3 and 4 define each market word once. One chart for the week, one chart for the live replay.

### 1. Team — 0:00 to 0:25, camera

I'm Aayush. I built the capacity-policy test harness for this hackathon: the ERCOT tape, the scorecard, the policy plug-in, and the live replay. The job was to make a change to Base's capability number something you can replay before it is live.

### 2. Elevator pitch — 0:25 to 0:50, camera, then glance at the terminal

Base already reports how many megawatts of ECRS and Non-Spin its batteries can hold, and it does real time reporting under the ADER program to ERCOT of how much capacity they can serve for Auxillary Services in the day ahead + real-time markets. From talking with Engs on Saturday, there seemed to be some improvements we could make on simulating the algorithms that decide how much capacity to report on real historical ERCOT data. If we can more reliably forecast the past, we can avoid depriving the grid of electrcity in the future; under-serving has tangible human consequences so we cannot mess these algorithms up. My project replays any algorithm on the real ERCOT tape since RTC+B and prints the chance of being short by at least X megawatts against the revenue that policy gave up. So now I'm going to run one real day.

### 3 and 4. Live demo, and how it is built — 0:50 to 4:15

Press Enter. Stay on the console until you click to the rankings chart.

**[SAY, over the run]**

This command tests one algorithm on 17 August 2026. The algorithm is a haircut. It reports 90 percent of the capacity it can see. It does not use the price. The prices come from ERCOT, the operator of the Texas grid.

**[SAY, table on screen. Point at P50, then P10.]**

ERCOT pays a battery to hold two reserves. ECRS must be deliverable for 1 hour. Non-Spin must be deliverable for 4 hours, so the same battery can offer much less of it.

The algorithm reports a capacity, K. D is what the homes can really deliver. The algorithm does not see D. This fleet is a test setup of 1000 homes, not Base's measured fleet. Twenty percent of each battery stays for the home.

P50 is the middle fleet. The algorithm sees only P50. P10 is a thin fleet: only 10 percent of outcomes have fewer homes. The same report is scored on every row. The algorithm cannot look at P10 and change K.

On P10, K is above D all day. A shortfall is capacity you promised and could not deliver when ERCOT calls on the reserve. The negative dollars are a placeholder penalty, not an invoice. From P25 up, the shortfall is zero. "Given up" is payment you did not collect because you reported less than D.

**[SCREEN]** `rankings.png` only.

This is the same test for 11 to 17 August, already run. Four algorithms. The haircut is the 90 percent rule. The independent newsvendor assumes each home fails alone, and reports more when the price is high. The correlated newsvendor assumes a whole region can fail together. The reliability target caps the chance of a shortfall.

On the P50 fleet, the week's shortfall is zero, so a stricter target only gives up payment. Raising the sales cap from 100 to 500 megawatts does not change the order. This fleet is too small to fill the cap. Cutting Non-Spin from 4 hours to 2 hours does. The correlated newsvendor then beats the haircut, because the same stored energy supports more megawatts.

**[SCREEN]** `data/replay/chaos/timeline.png`.

Same morning, on a 2-second clock. A region outage drops both lines at once. Two killed processes stay in the report until their data is 180 seconds old. That is Base's stale-telemetry rule. The log said reaction 180 seconds and zero violations of the home's reserve. A Base algorithm can be its own program. If it crashes, the test records the fault and finishes the day.

### 5. So what — 4:15 to 4:45, camera or the chart

This test is for the people who choose the capacity Base reports to ERCOT. On a thin fleet, the 90 percent haircut is short. On the middle fleet, it leaves payment uncollected. When Non-Spin changes from 4 hours to 2 hours, a different algorithm ranks higher. Next, Base can point the same command at its own program, replace the 1000-home setup with its own telemetry, and replace the placeholder penalty with the real charge. The megawatt-hour score does not need to change for any of those.

## If they ask

**Is the dollar number true?** No. The shortfall in megawatt-hours does not use the unknown penalty. The negative dollars use a placeholder. Three placeholder formulas are in the setup file. Only the dollar columns change when you switch formulas.

**Does the algorithm see the future?** No. When a price forecast is attached, the algorithm can use only forecasts posted at or before the decision. The one look-ahead is the "top 1 percent" label for a scarce interval. That label uses the full stored history. The score calls that label an assumption.

**Why is the revenue chart a flat line at zero shortfall?** On the P50 fleet, every algorithm reported less than D whenever a call was drawn. The algorithms still differ by the payment they give up. P10 is where the haircut is short. A larger fleet, or a haircut above 90 percent, is how you move that line off zero.

**Can you run our program?** Yes. The program reads one description of the current interval and writes ECRS and Non-Spin capacity. If the program is late or crashes, the test uses the last good report and records the fault.
