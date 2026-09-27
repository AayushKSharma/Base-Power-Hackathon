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

Have these open behind it, in this order, so each switch is a click and not a hunt: `docs/insights/compare/charts/frontier.png`, `rankings.png`, `docs/insights/compare/comparison.md` scrolled to the first P10 ECRS table, `data/replay/chaos/timeline.png`, `docs/bench/bench.md`.

Do not run `make backtest`, `make bench`, or `make chaos` on camera. The week and the farm rewrite published files. Chaos takes about 90 seconds, which would blow the five minutes. You already have its log: reaction 180 seconds, recovery 0, floor violations 0.

## Script

Read the times as a ceiling. If you are late, drop the farm sentence, not the close.

### 1. Team — 0:00 to 0:25, camera

I'm Aayush. I built the capacity-policy test harness for this hackathon: the ERCOT tape, the scorecard, the policy plug-in, and the live replay. The job was to make a change to Base's capability number something you can replay before it is live.

### 2. Elevator pitch — 0:25 to 0:50, camera, then glance at the terminal

Base already reports how many megawatts of ECRS and Non-Spin its batteries can hold. There is no solid bench for a change to that number, and under-serving a deployment is a reliability problem, not just a dollar problem. This replays any policy on the real ERCOT tape since RTC+B and prints the chance of being short by at least X megawatts against the revenue that policy gave up. I'm going to run one real day.

### 3 and 4. Live demo, and how it is built — 0:50 to 4:15

Press Enter. Stay on the console. The first paragraph fits the 11 seconds the command takes. Point at rows after they appear.

**[SAY, over the run]**

One command. A 90 percent haircut, 17 August 2026. Python, scored offline against ERCOT's public real-time prices, from December 2025 through this month. The fleet is not in that data. It is a scenario file: 1,000 homes, Base Core 20 kilowatts and 39.2 kilowatt-hours, 20 percent reserved for the member. A typo is an error, not a default. At a typical 60 percent charge that home can hold about 16 kilowatts for an hour of ECRS and about 4 for four hours of Non-Spin.

**[SAY, table on screen. Point at the header, P10, then P50.]**

The policy saw the P50 fleet once, and that decision is scored against every quantile. It never sees the true deliverable. Floor violations are zero: the member reserve is clamped. On P10 the same report is over-sold all day, shortfall is a few tenths of a megawatt-hour, and net dollars go negative. That negative is a placeholder charge, not an invoice. We do not know what an ADER pays for a short deployment, so three formulas sit in the scenario, and only the dollars move when you switch them. From P25 up, shortfall is zero and the revenue given up grows. Planned for the median, short on a thin fleet, leaving money on a thick one.

Deployments are drawn, 2 percent calm and 30 percent in a scarce interval. Scarce means the price beat that product's 99th percentile in the stored tape, which looks ahead, and is labeled as an assumption.

**[SCREEN]** `frontier.png`, then `rankings.png`.

A week of four policies takes minutes, so this is the same scorecard already run for 11 to 17 August. Each reference policy has its own belief. The scorecard grades the scenario. On the fleet they saw, weekly shortfall is zero, so the frontier is flat and a tighter reliability target only gives up revenue. The cap lifting to 500 megawatts does not reorder anyone. Non-Spin cut from four hours to two does: the correlated newsvendor passes the haircut, and that revenue roughly doubles because the same energy covers more megawatts. Policies on one scenario share the seed, so that gap is the policy.

**[SCREEN]** `data/replay/chaos/timeline.png`.

Same morning, two-second clock, one host per region. That run is 90 seconds, so this is its chart, and the console line was reaction 180 seconds, recovery 0, floor violations 0. Hosts killed at 2 seconds stay in the report until telemetry is 180 seconds old. An outage at 100 seconds drops both lines immediately. Recovery is zero only because nothing was deployed. The restart at 200 seconds reloads the coordinator from Postgres and the lines do not move.

The same day-run is a job on a Postgres queue, claimed with `SKIP LOCKED` and written once, so a killed worker cannot double-count. Postgres, not Temporal, because this has to run on a laptop. A Base policy is a process: JSON in, megawatts out. A crash counts as a fault and the day still finishes.

### 5. So what — 4:15 to 4:45, camera or the chart

This is for the people who change the number Base reports to ERCOT. The week shows a fixed haircut leaving money on the table while a looser policy is the one that is short on a thinner fleet, and it shows that ranking move when Non-Spin's duration changes. Next is their binary on `--policy`, their telemetry in place of the placeholder fleet, and the real shortfall charge in place of the three presets. The physical map does not have to be rebuilt to take any of those.

## If they ask

**Is the dollar number true?** No. Physical shortfall, over-sold MW-h, the exceedance curve, and revenue given up against the real reserve price do not use the unknown shortfall charge. The three presets are the three answers still open in question 3.1.

**Does the policy see the future?** No. Forecast inputs, when a store is attached, are vintages posted at or before the decision. The scarcity percentile used to label an interval is the one look-ahead in the market tape, and the report calls it an assumption.

**Why is the frontier flat?** On this placeholder fleet, every reference policy was conservative enough that the P50 fleet covered the award whenever a deployment was drawn. The bench still separates them on revenue given up, and it shows under-serving on the quantile they did not plan for. A larger fleet, a storm file, or a looser haircut is how you move the line off zero.

**Can you run our binary?** A process that answers the hello and one observation per interval. Same timeout and fallback as the Python template.
