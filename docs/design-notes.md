# Design notes

Each note is a choice the harness is built around, and the reason for it. Open engineer questions stay open: [docs/questions.md](questions.md). Scenario defaults and the question that would replace each one are listed in the [README](../README.md#scenario-assumptions).

## Why not a day-ahead model after RTC+B

RTC+B went live on Dec 5, 2025 and removed the Failure-to-Provide charge. A day-ahead ancillary-service award is a financial position. The gap between that award and the real-time award settles symmetrically at the real-time clearing price (RT MCPC):

```
profit = p_DA·C + p_RT·(A − C)    ⇒    E[profit] = C·(p_DA − E[p_RT]) + E[p_RT·A]
```

The day-ahead quantity C that maximizes expected profit does not depend on how the fleet fails. The one-sided exposure moved to real time: the fleet reports capability, SCED awards against that number, and a shortfall shows up when ERCOT deploys. The harness scores that real-time report. Question 0.1 asks where Base actually loses money since RTC+B; the answer would say whether this settlement reading is the one to keep. Question 4.2 asks whether Base still sells reserves day-ahead.

## One day as the unit of simulation

A run is a range of ERCOT operating days (Central Prevailing Time). The runner scores each day on its own and combines the per-day scorecards (`Scorecard.combine`). A date-range run therefore equals the combination of its single-day runs. The scorecard stores sums and counts for each day, and totals are those sums. Rates are derived when shown, so combining days does not depend on the order or on which worker scored which day.

That is what lets the run farm split a sweep into one job per policy, scenario, day, and seed, and still match a sequential run. A deployment window that crosses midnight uses that day's draws, so the day stays closed. The harness also replaces an external policy process at the day boundary and forgets its last good decision, so a fallback cannot leak from one day into the next. A policy that keeps state anywhere except its own memory still has to drop that state at the boundary; the protocol says so.

## Common random numbers

Draws come from a seeded generator tree, `harness.RandomStreams`. The stream key is the scenario name, the seed, the operating day, and a stream name (`soc`, `availability`, `home_dropouts`, `region_outages`, `deployments`). The reference policies that sample use a separate stream, `reference_policy`. The policy name is not part of the key. Two policies on the same scenario, seed, and day see the same SOC, the same available homes, the same dropouts, and the same deployments. `harness compare` relies on that: dollar and shortfall differences inside one scenario are the policies, not a fresh set of failures.

The scenario name is part of the key. `baseline`, `caps_lifted`, and `nonspin_2h` do not share draws, even where their failure numbers match, because each file's name is a different key. A ranking that moves between presets can be the knob (the cap, the duration) or the different draws. The published week says so: the close race on baseline is too narrow to treat as paired across presets. Sharing draws across scenario files would mean taking the scenario name out of the key, which would also make a renamed file replay an old day's draws.

## Observed state and true state

The policy never sees the deliverable MW it is scored against. Each interval it receives an observation. In the default `typical` view that observation is the P50 fleet, one decision per interval, and that same decision is scored against every quantile's true fleet. `per_case` shows the policy each quantile's own fleet and decides once per quantile. Stochastic mode has one case and always decides for that case.

What the observation's `fleet` section reports, per region, is the state at the start of the interval: homes online, homes whose telemetry is older than `telemetry_stale_s`, homes in backup, and the online homes' energy, inverter kW, and capability. Stale and backup homes are left out of the online totals. A home that dropped out more recently than the stale threshold still looks online. That gap is the difference between the MW the policy can see and the MW the fleet can produce.

True deliverable MW (D) counts only homes that stay available for the product's whole duration from the interval's start. Over-sold MW-h is `max(K − D, 0)` on every interval. Shortfall MW-h is that gap only on intervals drawn as deployed, and it uses the award `min(K, cap × cap share)` rather than the raw report. The `typical` view is there so a policy can be right about the fleet it saw and still be short on a thinner quantile. Question 1.1 and question 6.2 ask what Base's policy actually consumes; question 2.3 asks what a dark device is assumed to be.

## Postgres queue and Temporal

The run farm's queue is Postgres. The reasoning is [ADR 0015](adr/0015-run-farm-resilience.md); this note follows it.

A sweep is policies × scenarios × operating days × seeds. A worker claims the oldest queued or expired job with `SELECT … FOR UPDATE SKIP LOCKED`, scores that one day outside the claim transaction, and inserts the scorecard in the same transaction that marks the job done. The result's identity is the policy name, the policy version, the scenario content hash, the day, and the seed. The insert is conditional on the lease owner and the attempt, and `ON CONFLICT DO NOTHING`, so a retried or raced day is stored once. A killed worker leaves the lease to expire; the day runs again and is not counted twice.

Base uses Temporal in BaseOS. Question 5.4 asks what Temporal does there and where Base chose not to use it. Temporal would give the farm a durable history, activity retries, heartbeats, and a view of which day is running. The farm uses a smaller piece: a queue, a lease, a retry time, and an idempotent scorecard write. Temporal would not make a harness day deterministic, and it would not by itself stop two workers from recording the same day. The exactly-once part is the result key and the fence, and those stay in Postgres because that is where the scorecards are. One database lets the claim and the result commit together. The harness also has to run from a laptop against a copied market dataset, so a workflow service would be a second thing to operate for a queue that is visible in SQL.

If this sweep ran inside BaseOS, it would be a workflow and each day an activity with a heartbeat. Completing the activity would still store the scorecard under the same identity key. BaseOS would replace the compose Postgres and the way farm chaos stops workers. It would not replace the scorecard key.

Farm chaos (`harness chaos`) kills workers mid-job and before commit during a sweep. That is separate from live replay. `harness replay` has no chaos-schedule flag; `make chaos` plays the demo day, and the seeded live-replay schedule is the stretch still to land on that command.

## Shortfall-cost presets

Question 3.1 asks what an ADER pays when a deployment under-delivers, and it is unanswered. The three possibilities named there are the three scoring presets. `scoring.preset` selects one. Physical shortfall MW-h does not use the preset.

| Preset | Formula | Question 3.1 |
|---|---|---|
| `energy` (the baseline) | Load-zone real-time price × shortfall MW × product duration, on intervals whose load-zone price is present | (a) energy at the load-zone price |
| `energy_spd` | The `energy` charge, plus `spd_per_mwh` on the shortfall beyond the lesser of 3% of the award or 3 MW | (b) Set Point Deviation on top of (a) |
| `imbalance` | RT MCPC × shortfall MW × 5/60 h, on intervals whose settlement price is `ok` | (c) the gap bought back at the real-time reserve price |

`compliance_per_mw` (baseline $500/MW) is added on top of every preset, per MW short. That number is the placeholder in the handoff's toy and in the list under question 6.6. Question 3.2 asks how real the risk of losing ADER qualification is; it is unanswered, so the $500 does not represent a confirmed charge.

The 3.1 answer so far says money is only part of the harm, and asks for a map of under-serving against revenue given up. The tolerance table and the exceedance curve are that map. Net dollars stay on the scorecard as a secondary view under whichever preset is configured. Changing the preset changes the dollars. It does not change shortfall MW-h, over-sold MW-h, or the overstatement rate.
