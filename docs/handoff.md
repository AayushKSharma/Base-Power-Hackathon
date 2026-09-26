# Base Power Hackathon: Project Handoff

_Last updated: 2026-09-26. Status: positioned as a **test harness for capacity policies**, benchmarked against real ERCOT data. No project code yet. Talk to a Base engineer before building ([questions.md](questions.md))._

**Research behind this doc:**
- [ERCOT ancillary services after RTC+B](research/ercot-ancillary-services.md)
- [How Base Power participates in ERCOT](research/base-power-ercot-participation.md)
- [Synthesis: why the original premise broke](research/synthesis-refined-idea.md)

## 1. Event context

- **Event:** Base Power Austin hackathon, Sep 25–27, 2026, at Base HQ (part of Deep Tech Week). It's a **hiring event**, and Base engineers judge every track.
- **Base Power:** builds, installs, and runs home batteries as one coordinated fleet that helps balance ERCOT. Base describes itself as a *power company*, not a battery company, and calls its customers **members**.
- **Judging:** based on a **5-minute demo video and the codebase**, not a live demo. "Real, working systems, not slide decks or API wrappers."
- **One project can enter up to 2 tracks.**

### Tracks
1. **Open Grid Data:** build on ERCOT public data (prices, load, generation, congestion). "Show us what you can see that most people miss."
2. **Orchestration:** coordinate many independent things (agents, jobs, workers). Judged on how it holds up when pieces fail.
3. **Most Commercializable:** something that could ship as a product on top of Base, for members. "Show off your taste."

### Rubric (100 points)
| Area | Points | Sub-criteria |
|---|---|---|
| Technical execution | 30 | Completeness 15 (core workflow runs without crashing), Technical depth 15 |
| Fit to track | 30 | The problem 15, the "why" 15 |
| Value and impact | 20 | Insight quality 10 (non-obvious and useful), Usability 10 (could Base use it tomorrow?) |
| Innovation and execution | 20 | Creativity and polish 10, Performance 10 (speed and scale) |

### How Base plays in ERCOT
- **Retailer:** Base Texas REP, LLC, a Retail Electric Provider (PUCT #10338).
- **Market scheduler:** Base runs its **own QSE** (qualified scheduling entity, the company that bids and settles with ERCOT).
- **ADER pilot:** Base is the largest participant in ERCOT's pilot for aggregated home batteries (ADER). It claims about 71% of pilot MW.
- **Products:** ADERs can sell energy plus only **Non-Spin and ECRS** (no Regulation; RRS "under consideration").
- **Pilot caps:** 100 MW system-wide per product, and one QSE can hold at most 90%. ECRS was at 97.3 of 100 MW in Aug 2026.
- **Fleet:** about 205 MW nameplate, with about 81 MW in ADER groups. Growing about 2 MW per day after a $1B raise (Aug 2026).
- **Hardware:** Base Core, **20 kW / 39.2 kWh**.
- **Backup:** Base keeps at least a **20% SOC** reserve and promises 5 or 10 hours of backup. A home in an outage disconnects from the grid and **never exports**.
- **Stack:** BaseOS runs on **Go, Python**, **Temporal**, AWS, and MQTT to devices. Telemetry older than **180 s** counts as stale.

## 2. Chosen direction: a capacity-policy test harness (Orchestration + Open Grid Data)

**Pitch:** "You already have a capacity policy. This harness tells you what it would have earned and risked on every real ERCOT day since RTC+B, and what it will do under conditions you haven't lived through yet: a fleet 10x bigger, a regional blackout during scarcity, the pilot caps lifting, Non-Spin going to 2 hours. Plug your policy in, get a scorecard."

### Positioning: what we are and aren't claiming
- **Not claiming** our policy beats Base's. Base has a trading desk, a Markets team, and fleet data we'll never see. A weekend policy would lose, and nobody swaps a production policy for one.
- **Claiming** that any policy is safer to change when you can replay it on real market data under correlated failures, *before* it's live. Mature teams under-invest in exactly this kind of evaluation tooling, and it gets more valuable as the fleet and the rules change.
- **Reference policies** ship with the harness (fixed haircut, independent-failure newsvendor, correlated Monte Carlo). They exist to show the harness **tells policies apart**, not to be the answer.
- **Forward-looking scenarios** are the part no in-house system is tuned for yet, because the rules and fleet haven't changed yet.

**Fallback (Commercial + Grid Data):** a member-facing app ("your battery earned $X this week, you have Y hours of backup, here's why it discharged at 5pm") built on the same replay engine. Easier to polish, thinner engineering.

**Parked:** Distributed Compute (GPU sites on the fleet that back off during grid events). Ask the engineer about it, but building a product there needs too many assumptions we can't check.

### Why the original "how much to promise day-ahead" idea broke
RTC+B (live Dec 5, 2025) **deleted the Failure-to-Provide charge**. A day-ahead AS award is now a financial position, and any gap to the real-time award is settled **symmetrically** at the real-time clearing price (RT MCPC):

```
profit = p_DA·C + p_RT·(A − C)    ⇒    E[profit] = C·(p_DA − E[p_RT]) + E[p_RT·A]
```

The best day-ahead C doesn't depend on failures. The one-sided risk moved to **real time**: the fleet reports capability to ERCOT every 2 s, and overstating it only shows up when ERCOT deploys. That real-time decision is what the harness scores.

## 3. What the harness scores

### Terms
- **ERCOT:** Electric Reliability Council of Texas, the grid operator and market.
- **AS (ancillary services):** grid-balancing reserves. ERCOT pays for **capacity held ready** ($ per MW per hour).
- **ECRS:** a reserve product with 10-minute notice. Batteries must sustain it for **1 h** (§8.1.1.3.4; §3.17.4 says 2 h, a known inconsistency).
- **Non-Spin:** a reserve product that must be sustained for **4 h** (2 h once NPRR1309 is implemented).
- **ADER:** Aggregate Distributed Energy Resource, the ERCOT pilot for aggregated home devices. It reports capability every 2 s.
- **RT MCPC:** real-time clearing price for an AS product, in $/MW-h. Exists only since Dec 5, 2025.
- **SCED:** ERCOT's 5-minute real-time dispatch, which awards AS against reported capability.
- **SOC:** state of charge.
- **Deployment:** ERCOT calling on awarded reserves, which happens in scarcity.
- **Capability K:** the MW of a product the fleet reports it can provide right now. This is **what a policy outputs**.
- **Deliverable D:** what the fleet can actually sustain for the product's duration if deployed.

### The policy's job
Each interval, a policy reads fleet and market state and outputs K for ECRS and Non-Spin.
- **Upside is linear.** Base offers at about $0.01, so assume award ≈ K up to its cap share, earning p_RT × K per hour.
- **Downside is one-sided.** If ERCOT deploys and D < K:
  - the shortfall is priced at the energy price λ during scarcity, over the h-hour window;
  - there is compliance risk: telemetry validation, "continuing failure to perform", and possible loss of ADER qualification.

```
D = Σ over homes still online through the window of  min(20 kW, (SOC − 20%)·39.2 kWh / h)
```

- Energy, not power, binds Non-Spin: 4 h means about 4 kW per home at 60% SOC.
- Homes drop out from backup mode, lost connectivity, firmware cohorts or storms, **correlated by region**.

### The scoring model
The scoring model is where the harness's credibility lives, so every assumption is a named config value and the unverified ones are flagged.

```
per interval:  AS revenue     = min(K, cap share) · p_RT · Δt
if deployed:   shortfall cost = (λ·h + c) · max(K − D, 0)     ← λ-based charge and c are UNVERIFIED (questions 3.1, 3.2)
```

### Scorecard (per policy × scenario)
| Metric | Why Base cares |
|---|---|
| Net $ (AS revenue − shortfall cost) | The headline |
| Shortfall MW-h during deployments | Physical reliability, independent of the price assumptions |
| Overstatement rate: % of intervals where K > D | Compliance-risk proxy |
| Regret vs. a hindsight oracle (K = true D) | How much room is left, a fair ceiling |
| Backup-floor violations | Must be zero; members come first |
| Reaction latency: region goes dark → K reflects it (p50/p99) | Orchestration quality, live mode only |

### What a reference policy looks like (newsvendor)
Variables:
- p = RT MCPC
- d = Pr(deploy this interval)
- λ = energy price at deployment
- h = product duration (hours)
- c = compliance cost per MW short

```
gain of one more MW = p − d·(λ·h + c)·Pr(D < K)    ⇒    K* = quantile(D, α),   α = p / (d·(λ·h + c))
```

When scarcity pushes p, d and λ up together and regions go dark at the same time, there's no closed form, so the "correlated" reference policy solves it by Monte Carlo (§8).

### Toy result: proof that the harness tells policies apart
§8 code, illustrative parameters, 1,000 online homes, 10 regions:

| Product | Regime | Nominal | Fixed 90% rule: K / EV per hour | Newsvendor: K / EV per hour |
|---|---|---|---|---|
| ECRS (1 h) | Calm | 14.6 MW | 13.2 MW / $66 | 14.5 MW / $71 |
| ECRS (1 h) | Storm | 14.6 MW | 13.2 MW / $1,309 | 11.3 MW / $1,609 |
| Non-Spin (4 h) | Calm | 3.8 MW | 3.4 MW / $17 | 3.8 MW / $18 |
| Non-Spin (4 h) | Storm | 3.8 MW | 3.4 MW / **−$98** | 2.6 MW / $376 |

A fixed haircut is too conservative when calm and too aggressive in storms, and that is the kind of finding the harness should surface for *Base's* policy. The parameters are made up; in the harness they come from real prices and a config file.

## 4. Scenarios and data (the Grid Data insight)

### Market data (real)
Data window: from **Dec 5, 2025**. Real-time AS clearing prices don't exist before that date.

| Data | ERCOT report | gridstatus call |
|---|---|---|
| RT MCPC, 15-min settlement price | NP6-331-CD | `get_mcpc_real_time_15_min` |
| RT MCPC, weekly history | NP6-795-ER | — |
| RT MCPC, 5-min | NP6-332-CD | `get_mcpc_sced` |
| AS demand curves (ASDCs) | NP4-212-CD | `get_as_demand_curves_dam_and_sced` |
| AS capability incl. SOC limits | NP6-328-CD | `get_as_total_capability` |
| Load-zone real-time energy prices | — | — |
| Weather / outage stress | NOAA alerts, or utility outage history if available | — |

The gridstatus real-time methods only reach a few days back, so pull history from the NP6-795-ER weekly files.

**Deployment proxy:** under RTC, "deployment" means SCED running short of ECRS or Non-Spin. Derive deployment intervals from public data. How to do this is still open (§7).

### Failure scenarios (config)
A failure-model config Base can replace with its own telemetry:
- independent per-home dropout
- regional dropout (feeder, cell carrier, firmware cohort)
- storm-correlated dropout, with rates rising under grid and weather stress
- homes entering backup mode during local outages

### Forward-looking scenarios (config): the part no in-house system is tuned for yet
| Scenario | Knob |
|---|---|
| Fleet at 10x (the $1B raise) | Homes 1k → 50k, more regions |
| Pilot becomes permanent rules; caps lift | AS cap 100 MW → 500 MW or none |
| NPRR1309 (DRRS) | Non-Spin 4 h → 2 h |
| ECRS duration ambiguity | ECRS 1 h vs. 2 h |
| RRS opens to ADERs | Add a 0.5 h product |
| Hurricane over Houston during scarcity | Correlated regional loss + price spike |

**Rule of backtesting:** a policy only sees information available at decision time. Never the deployment or price that came next.

**Outputs:**
- A scorecard per policy × scenario.
- Where each policy's losses concentrate: which intervals, which regimes.
- How the ranking of policies **flips** under the forward-looking scenarios. That's the non-obvious insight.

**Honest framing:** prices are real, failure rates are assumptions. Report relative differences between policies. If the fixed haircut is near-optimal on real data, say so; that's still a useful result for Base.

## 5. System architecture

The harness has two layers of orchestration: a live fleet simulation that the policy runs inside, and a run farm that fans out thousands of policy × scenario backtests.

### Policy plug-in interface
- **Language-agnostic**, because Base writes Go and Python. A policy is a separate process that speaks a small protocol: JSON over stdin/stdout, or HTTP.
- **Input:** time, market state (RT MCPC, ASDCs, λ), rules (durations, cap share), and fleet state.
- **Output:** `{ecrs_mw, nonspin_mw}`.
- **Tradeoff to decide:** per-home fleet state is about 5 MB of JSON per tick at 50k homes. Offer an aggregated-by-region view as the default, with per-home data opt-in.
- **Policies are untrusted plug-ins.** On a timeout, crash or garbage output, the harness falls back to the last good K, or 0, and records it in the scorecard. A policy that misses deadlines should score worse.

### Two modes
| Mode | Cadence | Span | Purpose |
|---|---|---|---|
| **Backtest** | SCED 5-min intervals | Dec 2025 → now, many scenarios | Scorecards; fast, and parallel across the run farm |
| **Live replay** | 2 s ticks, like ADER telemetry | One real scarcity day | Policy runs inside the real orchestrator with chaos; measures reaction latency |

### Live fleet simulation (the system under test)
- A coordinator plus N battery agents as separate, killable processes.
- **Reconciliation loop:** repeatedly compare desired MW with delivered MW and fix the difference (the Kubernetes controller pattern).
- **Heartbeats and leases:** an agent that misses heartbeats is presumed dead, and its share is reassigned.
- **Stale telemetry:** anything older than 180 s is excluded, matching Base's rule.
- **Idempotent, versioned commands:** retries and duplicates never cause a double discharge.
- **Constraints:** respect each home's backup floor, its 20 kW limit, and backup mode.
- **Coordinator crash recovery:** rebuild state from Postgres. This is the most important failure test.
- **Seeded chaos script:** worker crashes, dropped messages, delayed telemetry, a regional blackout, a network partition, and a coordinator restart, in the same order every run.

### Run farm
- Policy × scenario × day runs are jobs in a Postgres queue (`SELECT … FOR UPDATE SKIP LOCKED`).
- Workers can die mid-run: jobs are leased, retried, and deterministic by seed.
- Results are written idempotently, so a retried job never double-counts.
- **Benchmark:**
  - scenario-days per minute at 1, 8 and 32 workers;
  - live mode with 10k–50k agents: time until K reflects the loss of 20% of agents, and time back to the delivered target (p50/p99).

### Stack
- Python for scoring and data, with `gridstatus` for ERCOT data. Go is optional for agents if performance needs it.
- Postgres as the source of truth.
- **Base uses Temporal.** The run farm is a natural fit for it, so either use it and explain exactly what it guarantees (durable execution, retries, what it doesn't do for you), or use a plain Postgres queue deliberately and say why.

```
                ┌──────────── scenario config (failures, rules, fleet size) ────────────┐
ERCOT data ──►  │  replay clock ──► market state ─┐                                     │
(RT MCPC,       │                                 ├──► POLICY (plug-in) ──► K ──► SCED  │
 ASDCs, λ)      │  fleet sim ──► fleet state ─────┘     (Base's, or a         (award)  │
                │   ▲  coordinator + N agents            reference)              │      │
                │   └──── deployment ◄───────────────────────────────────────────┘      │
                └──► scorer ──► scorecard (net $, shortfall MW-h, overstatement, regret) ┘
run farm: policy × scenario × day jobs ──► Postgres queue ──► workers (killable) ──► results
```

## 6. Deliverables checklist

**Codebase**
- [ ] README with an architecture diagram, plus these commands:
  - [ ] `make backtest`
  - [ ] `make replay`
  - [ ] `make chaos`
  - [ ] `make bench`
  - [ ] `harness run --policy ./my_policy --scenario storm_houston`
- [ ] Three reference policies and one example policy in Go, to prove the interface is language-agnostic
- [ ] Failure model and scenarios as config files Base can replace
- [ ] Tests:
  - [ ] the same seed gives the same scorecard (determinism)
  - [ ] a policy that times out or crashes is handled and penalized
  - [ ] a killed run-farm job is retried without double-counting
  - [ ] stale telemetry (180 s) is excluded
  - [ ] the backup floor is never violated
  - [ ] homes in backup mode never count toward deliverable MW
  - [ ] coordinator restart
- [ ] Design notes: each tradeoff and why, including "why not a day-ahead model" (RTC+B settlement) and the unverified scoring assumptions

**5-minute video**
| Time | Content |
|---|---|
| 0:00–0:40 | The problem: capacity policies are hard to change safely; the rules and the fleet are about to change a lot |
| 0:40–1:40 | The insight from real post-RTC+B data: where a fixed haircut loses money, and how policy rankings flip under forward-looking scenarios |
| 1:40–3:10 | Live replay of a real scarcity day with a policy plugged in: regional blackout, K drops, coordinator killed and recovers, committed vs. delivered MW |
| 3:10–4:00 | Run farm: thousands of scenario-days, workers killed mid-run, scorecards still exact; benchmark numbers |
| 4:00–5:00 | How Base plugs in its own policy and telemetry tomorrow, plus architecture |

**Suggested timeline:**
1. Data pull and scoring model.
2. Policy interface and reference policies.
3. Backtest mode and first scorecards (insight check).
4. Live fleet sim with chaos.
5. Run farm and benchmark.
6. **Feature freeze**, docs, then record the video (leave time for retakes).

## 7. Open questions and next steps

- [ ] **Talk to a Base engineer** using [questions.md](questions.md). For the harness, the deciding questions are:
  - How do they test a policy change today?
  - What does their policy consume and produce (the interface)?
  - What makes them trust a score?
- [ ] **Unverified:** the actual charge to an ADER (Load Resource) that under-delivers on an ECRS or Non-Spin deployment. Until confirmed, use λ × MW short plus a compliance-cost parameter c, and flag it in the scorecard.
- [ ] **Deployment proxy:** find how to identify ECRS/Non-Spin deployment or scarcity intervals in public data after RTC+B.
- [ ] **Solo or team?** Solo: skip the dashboard and use scorecards in the terminal plus one matplotlib chart. Team: split scoring and backtest from the live simulation and run farm.
- [ ] **Failure model scope:** independent plus regional (quick), or also storm-correlated using weather data (more realistic, costs more time)?
- [ ] Get an ERCOT API key and install `gridstatus`, then pull RT MCPC (NP6-795-ER, NP6-331-CD), ASDCs and load-zone real-time prices.
- [ ] Confirm the harness separates policies on real data **before** building the fancy parts.

## 8. Toy simulation code (reproduces the table in §3)

```python
import numpy as np
rng = np.random.default_rng(1)
N, R = 1000, 10                   # homes currently online, failure regions
KWH, KW, FLOOR = 39.2, 20.0, 0.20 # Base Core: 39.2 kWh, 20 kW, 20% backup floor
soc = rng.beta(6, 4, N)           # current SOC, mean ~0.6
region = np.arange(N) % R

def home_mw(h):                   # sustainable MW per home for an h-hour product
    return np.minimum(KW, np.maximum(0, (soc - FLOOR) * KWH) / h) / 1000

def deliverable(h, q_home, q_region, n):
    """MW still deliverable if ERCOT deploys, after dropouts during the window."""
    cap = home_mw(h)
    reg_dark = rng.random((n, R)) < q_region
    home_drop = rng.random((n, N)) < q_home
    alive = ~(reg_dark[:, region] | home_drop)
    return alive.astype(np.float32) @ cap.astype(np.float32)

def ev(K, D, p, d, lam, h, c):
    return p * K - d * (lam * h + c) * np.maximum(K - D, 0).mean()

def best_K(D, p, d, lam, h, c):
    grid = np.linspace(0, D.max(), 800)
    evs = [ev(K, D, p, d, lam, h, c) for K in grid]
    i = int(np.argmax(evs)); return grid[i], evs[i]

C = 500.0  # compliance cost per MW short (placeholder)
for prod, h in [("ECRS", 1), ("NonSpin", 4)]:
    nominal = home_mw(h).sum()
    for name, qh, qr, p, d, lam in [("calm", .01, .005, 5, .02, 300),
                                    ("storm", .04, .08, 150, .30, 3000)]:
        D = deliverable(h, qh, qr, 20_000)
        K, e = best_K(D, p, d, lam, h, C)
        Kf = 0.9 * nominal
        print(f"{prod:7s} {name:5s} nominal={nominal:5.2f}MW  "
              f"fixed90%: K={Kf:5.2f} EV=${ev(Kf,D,p,d,lam,h,C):8.0f}/h  "
              f"newsvendor: K={K:5.2f} EV=${e:8.0f}/h  Pr(short|deploy)={np.mean(D<K):.3f}")
```

## Sources
Full citations, most from ERCOT Nodal Protocols §3, 4, 6 and 8, are in the research files linked at the top. Also:
- [ERCOT Developer Portal: Using the API](https://developer.ercot.com/applications/pubapi/user-guide/using-api/)
- [gridstatus ERCOT API examples](https://opensource.gridstatus.io/en/stable/Examples/ercot_api/index.html)
- [Electrek: Base Power raises $1B](https://electrek.co/2026/08/03/base-power-raises-1b-to-roll-out-its-giant-new-home-battery/)
- [Solar Power World: Base begins manufacturing 39.2-kWh battery](https://www.solarpowerworldonline.com/2026/08/base-power-begins-manufacturing-39-2-kwh-residential-battery-in-texas/)
