# Synthesis: what the research changes about the reliability-pricing idea

_2026-09-26. Built from [ercot-ancillary-services.md](ercot-ancillary-services.md) and [base-power-ercot-participation.md](base-power-ercot-participation.md). Citations are in those files._

## 1. The original premise is wrong under current ERCOT rules

The handoff model assumed an **asymmetric** payoff: `profit = p·C − P·max(C − A, 0)`, where C is the promise and A is what the fleet can actually deliver. That asymmetry is the only reason headroom is worth paying for.

RTC+B (live Dec 5, 2025) deleted the Failure-to-Provide charge. A day-ahead AS award is now a **financial** position. Real-time SCED (ERCOT's 5-minute dispatch) re-awards AS, and the difference is settled **symmetrically** at the real-time clearing price for that product (the RT MCPC):

```
profit = p_DA·C + p_RT·(A − C)
E[profit] = C·(p_DA − E[p_RT]) + E[p_RT·A]
```

With a symmetric payoff the best C **doesn't depend on the failure distribution**: the second term doesn't involve C. The day-ahead decision is just a bet on the day-ahead vs. real-time price spread. The handoff predicted exactly this when it said "in plain energy trading … the best promise doesn't depend on failures at all". Plain energy trading is what AS settlement now looks like.

**Base facts that narrow the problem further:**
- Base is its own QSE (the company that bids and settles with ERCOT).
- It participates as an **ADER** (the pilot for aggregated home devices). ADERs can sell only **Non-Spin and ECRS**, plus energy.
- The pilot caps AS at **100 MW per product** system-wide, and one QSE can hold at most **90%** of that. ECRS was at 97.3 MW in August 2026.
- Base's fleet is about 205 MW nameplate, so AS volume is **capped by the rules, not by the fleet**. Most of the fleet's value comes from real-time energy.
- Base offers some AS at about $0.01. That suggests it acts as a price-taker for its capped AS volume, but this is unverified.

**Bottom line:** "How much AS to promise day-ahead given correlated failures" is not a problem Base has in that form. Pitching it to Base engineers would signal we didn't read the rules. Better to find that out now than in the demo.

## 2. Where reliability risk actually lives now

The asymmetry didn't disappear. It moved to **real time and to compliance**.

1. **Telemetered capability.** An ADER tells ERCOT its available capability every 2 seconds, and SCED awards AS against that number. If the fleet reports more than it can deliver and ERCOT then deploys (ECRS or Non-Spin), two things follow:
   - **Undelivered energy.** The missing MW-h show up as energy imbalance at the real-time energy price. Deployments happen during scarcity, so that price is high. Deployment-level charges for Load Resources are **unverified**; ask the engineer.
   - **Telemetry validation.** ERCOT checks telemetry against meters and requires it within 10% in at least 50% of intervals. It can revoke qualification after "a continuing failure to perform".
   - **Why it matters to Base:** Base holds about 71% of the pilot, so losing qualification means losing a whole revenue line.

   The upside of overstating is linear (about p_RT per MW-h). The downside is a cliff. **That is the newsvendor structure again**, now applied to the real-time capability number instead of the day-ahead promise.
2. **Correlated tail risk in the day-ahead position.** The expected value ignores failures, but variance doesn't. `p_RT·(A − C)` is worst when a storm knocks out regions (A falls) just as reserve demand curves push p_RT toward $5,000. A risk-constrained C, maximizing E[profit] subject to a limit on expected losses in the worst few percent of cases (a CVaR constraint), **does** depend on how failures correlate with price.
3. **Sharing SOC across uses.** Each MWh of stored energy can back a reserve award or be kept for backup; it can't do both:
   - **ECRS** needs 1 hour of energy per MW, **Non-Spin** needs 4.
   - Base keeps at least 20% SOC for backup and promises members 5 or 10 hours of backup.
   - During an outage, a home disconnects from the grid and never exports.

   A regional outage therefore removes capability **and** makes those homes' SOC untouchable.

## 3. Reframed project options

| | A′. Honest capability (recommended) | B′. Risk-constrained day-ahead position | C′. SOC allocation across energy / AS / backup |
|---|---|---|---|
| Decision | MW of Non-Spin/ECRS capability to telemeter each interval, and at which confidence quantile | Day-ahead AS position C with a CVaR limit | How fleet SOC splits between real-time energy, AS duration, and backup reserve |
| Why it's real for Base | Directly protects ADER qualification and deployment performance; uses their 180 s stale-telemetry rule, backup floor, and outage mode | Real, but volume is capped at ≤90 MW per product, so the stakes are small | This is their core Markets problem ("sequential decision making"); hard to do well in 48 h |
| Orchestration fit | Strong: heartbeats, stale telemetry, and dark regions feed the capability estimate live; on deployment, the coordinator splits MW across agents | Weak | Medium |
| Grid-data fit | RT MCPC and ASDCs (from Dec 2025), deployment history, weather for storm correlation | DA vs RT MCPC spread (NP4-188 vs NP6-331), tail events | Load-zone price, MCPC |
| Keeps from handoff | Failure model, newsvendor math, whole orchestrator, chaos tests | Monte Carlo joint (A, P) scenarios | Replay engine |

**Recommendation: A′, using B′ as the Grid Data insight chart.**
- **Pitch:** "Base's ADER telemetry is a promise to ERCOT renewed every 2 seconds. We compute it from live fleet health, with correlated failure domains, so a regional outage lowers the promise *before* a deployment exposes it."
- **Grid Data chart:** real-time AS price vs. regional outage stress since Dec 2025. It shows *when* overstating capability is most expensive.

## 4. Questions for the Base engineer (prioritized)

1. **Model check.** Since RTC+B, does Base's shortfall risk come from the day-ahead/real-time AS imbalance, or from real-time telemetry and deployment performance? Where do you actually lose money or sleep?
2. **Telemetered capability.** How do you turn fleet SOC and heartbeats into the ADER capability number? Do you haircut it, by how much, and is the haircut fixed or conditional (weather, time of day, region)?
3. **Correlated dropouts.** When a region loses power or connectivity, how fast does that reach the capability number? Do you model failure domains (cell carrier, feeder, firmware cohort, storm path)?
4. **Deployment failures.** What happens financially and in compliance if an ECRS or Non-Spin deployment under-delivers? Is it a base-point deviation charge, energy imbalance, or only validation risk?
5. **ALR or NCLR?** Which ADER model are the groups registered as? Is energy dispatched by SCED or self-scheduled?
6. **Day-ahead vs real-time.** Do you sell AS day-ahead or only in real time? Are the $0.01 offers yours, i.e. are you a price-taker for the capped AS volume?
7. **Binding caps.** With ECRS nearly at the 100 MW cap and your 90% share limit, is AS capacity-limited by rules rather than by the fleet? Does that make real-time energy the main value driver?
8. **Backup vs grid.** How do outage prediction and the 20% SOC floor interact with a live AS award? Has a storm (e.g. Winter Storm Fern, Jan 2026) forced you to drop AS?
9. **Durations.** Do you plan ECRS at 1 h or 2 h? The protocols disagree: §3.17.4 says 2 h, §8.1.1.3.4 says 1 h. Do you expect Non-Spin to move to 2 h under NPRR1309?
10. **Numbers.** How do "103 of 145 MW", the 80.6 MW across ADER groups, and ERCOT's 292.9 MW qualified reconcile?
11. **Hackathon fit.** Which would you rather see: a better capability estimator, or better orchestration on deployment? Would you swap your own telemetry into a config-driven failure model?

## 5. Changes to the handoff

- Replace §3's premise. The asymmetric payoff belongs to **real-time capability vs. deployment**, not to the day-ahead promise. P = real-time energy price at deployment, plus a compliance cliff.
- Narrow products to **ECRS (1 h) and Non-Spin (4 h)**, and model SOC duration limits and the 20% backup floor.
- Stale-telemetry threshold = **180 s**, matching Base's own rule.
- Backtest data window starts **Dec 5, 2025**. Real-time MCPC doesn't exist before that date.
  - Real-time AS prices: NP6-795-ER (weekly history) and NP6-331-CD (15-min settlement price).
  - Day-ahead AS prices: NP4-188-CD.
  - AS demand curves: NP4-212-CD.
- Base uses Temporal. Be ready to explain exactly what it guarantees, or deliberately use Postgres instead and say why.
