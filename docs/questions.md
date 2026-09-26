# Questions for a Base engineer

*Read top to bottom. We're positioned as a **test harness for capacity policies**. The ★ questions shape what the harness must model and how it plugs in; ask those first if time is short. The context lines are for you, not for reading aloud. The terms are defined at the bottom.*

## Answers so far (2026-09-26)

- **6.1 (how they test policy changes):** Base has no solid test bench. A harness that grades capacity algorithms against ERCOT and other public data is wanted.
- **1.1 + 6.2 (policy inputs and outputs):** no concrete answer. Assume an algorithm's inputs are live public data (current and past prices, forecasts, etc.) plus the fleet's current available power. The harness feeds real data wherever possible and **multiple fleet-state mocks (quantiles)**, and outputs an analysis and a data dump of how the algorithm performed.
- **3.1 (cost of under-delivering):** money is only part of it. Under-delivering AS is bad for the grid and harms people. Aim to under-serve as little as feasible without egregious revenue loss. **Map the tolerance for each algorithm:** the probability of under-serving by at least X MW per hour, against the revenue given up.
- **Found since:** Base's own ADER awards and telemetry are public with a 60-day delay (QSE `QBASTX`), in [public-data-for-grading-and-forecasting.md](research/public-data-for-grading-and-forecasting.md).

## Start here: what isn't solved yet (★)

**A ★ What's something that isn't mature yet, or that you wish you had time to build?**

> Context: a sanity check on the harness positioning. If they name something close to evaluation tooling or scenario testing, lean into it.

**B ★ Is Distributed Compute live? When there's a grid event, how does the compute on the battery fleet back off, and how fast?**

> Context: a job posting mentions GPU sites on the battery fleet, with a dispatch layer that decides when compute runs or backs off. It's parked as a direction (too many assumptions to build on), but worth hearing about. Only one job posting backs this, so confirm it's real.

## 0. Opener (★)

**0.1 ★ Since RTC+B went live in Dec 2025, where does Base actually lose money or sleep: day-ahead vs. real-time AS imbalance, under-delivery when ERCOT deploys, telemetry validation, or something else?**

> Context: RTC+B deleted Failure-to-Provide. Day-ahead AS is now bought back symmetrically at the real-time clearing price, so we think the real risk sits in real-time capability and deployment. The answer decides what the harness's scoring model must get right.

## 1. How capability gets reported

**1.1 ★ How do you turn fleet state into the capability number you report to ERCOT for ECRS and Non-Spin: SOC, heartbeats, homes in backup mode? Where is the capability decided/computed and what algorithms/decisions are involved in that process? how does it work ms by ms and second by second?**

> Context: ADERs report capability every 2 s. It's the number SCED awards against.

**1.2 ★ Do you haircut that number? Is the haircut fixed, or does it depend on weather, time of day, region, or price? Roughly how big is it today?**

**1.3 For each MW of ECRS you report, do you hold 1 hour or 2 hours of stored energy behind it?**

> Context: ECRS is a promise to hold output for a set time if called, and ERCOT's rules disagree on that time (§8.1.1.3.4 says 1 h, §3.17.4 says 2 h). A battery at 60% SOC with the 20% floor can offer 15.7 kW for 1 h but only 7.8 kW for 2 h, so the choice changes reported capability by 2x.

**1.4 NPRR1309 (the new DRRS product) cuts the Non-Spin duration from 4 h to 2 h. Do you plan for that change, and what do you expect it to do to your Non-Spin volume and price?**

> Context: the same battery goes from 3.9 kW of Non-Spin at 4 h to 7.8 kW at 2 h. The 100 MW pilot cap may still block the extra MW. This is one of the harness's forward-looking scenarios.

**1.5 The times when high SOC is needed are also the times when pricing surges and its most lucrative to promise capacity to ERCOT, how does the minimum charge you promise customers factor into the ERCOT capacity promises? How does the 20% SOC floor enter the AS duration math? Is the floor fixed, or dynamic with outage forecasts, e.g. pre-charging before storms?**

## 2. Failure modes

**2.1 ★ Which failure modes does your capability number account for: backup mode, lost connectivity, stale telemetry, low SOC, weather? Do you treat failures as linked by a shared cause (same substation, cell carrier, firmware version, storm path), or as independent per home?**

> Context: independent failures average out over thousands of homes, but linked failures remove a whole block of homes at once. Linked failures decide how much headroom a policy needs. The answer sets the structure of the harness's failure-model config.

**2.2 When a region loses power or connectivity, how fast does that reach the reported capability? Every 2 s, or on a slower loop?**

> Context: a slow loop leaves a window where Base reports MW it no longer has, and a deployment in that window causes a shortfall. The harness scores this delay as "reaction latency". If the answer is "at once", that metric matters less.

**2.3 What do you assume about a device that goes dark: last known state, zero, or a statistical estimate? What share of the fleet is dark on a normal day vs. during a storm?**

> Context: your blog treats telemetry older than 180 s as stale.

## 3. Deployment and penalties

**3.1 ★ When an ECRS or Non-Spin deployment under-delivers, what exactly happens financially? Do you buy the missing energy at the real-time load-zone price, pay a Set Point Deviation charge, or pay nothing beyond the normal AS imbalance?**

> Context. Example: ERCOT calls on 10 MW and the fleet gives 7 MW. The 3 MW gap could cost one of three things:
> - **(a) Energy at the load-zone price.** The homes draw that energy from the grid, and Base, as their retailer, pays the real-time price for the area (Houston, North, etc.). In scarcity that can reach $5,000/MWh.
> - **(b) Set Point Deviation.** ERCOT charges power plants and grid batteries a fee when they miss their 5-minute target by more than 3% or 3 MW. I could not confirm that it applies to ADER groups.
> - **(c) Nothing extra.** SCED awards less reserve next interval, and Base buys back the gap at the real-time reserve price.
>
> This is the biggest number in the harness's scoring model.

**3.2 ★ If the fleet under-delivers when ERCOT calls on it, how real is the risk that ERCOT revokes Base's qualification to sell ECRS or Non-Spin? Has Base come close?**

> Context: the pilot lets ERCOT revoke qualification after "a continuing failure to perform". Separately, ERCOT checks reported MW against meter data: within 10%, in at least half the intervals over 8 hours. Base holds about 71% of the pilot, so losing qualification costs a whole revenue line.

**3.3 How often do ERCOT deployments actually drain member batteries? When they do, how do you restore charge: at once, or on a schedule that waits for cheaper prices? Do members have any say in when their battery refills, or any guarantee beyond the 20% floor? Are members compensated for grid events, and how?**

> Context. Public facts:
> - Base owns the battery, and members can't override the charge/discharge schedule.
> - Base credits members for grid energy used to charge it.
> - Base says it recharges quickly even when prices are high, and that batteries rarely fall below 50%.
> - Members pay a fixed membership and rate (e.g. $19/mo on Oncor). I found no per-event payment.
>
> Refill speed sets how fast capability comes back after a deployment.

**3.4 Can you share any history of under-delivery: how often, the root causes, and did it ever change your policy?**

## 4. Market structure and strategy

> Context for the whole section: Base can earn from the grid in two ways. **Energy trading** means discharging when real-time prices are high and charging when they're low. **Reserves** means getting paid per MW per hour to stand ready (ECRS, Non-Spin). These questions check how Base is set up for each, and so what the harness can safely simplify.

**4.1 Our research says your ADER groups are Aggregate Load Resources (ALR), dispatched by SCED every 5 minutes. Is that right for all groups, or do some use the NCLR model?**

> Context: an ALR gets a SCED target every 5 minutes and sells energy plus reserves. An NCLR sells reserves only and is called by direct instruction. This decides what the harness simulates: 5-minute targets or rare instructions.

**4.2 Do you sell reserves in the day-ahead market, or carry them mainly through real-time awards?**

> Context: since Dec 2025, a day-ahead sale is only a financial position, and any gap is bought back at the real-time price. If Base sells mostly in real time, the harness can skip the day-ahead market.

**4.3 ERCOT's report shows ADER AS offers at about $0.01. Is that Base, and is price-taking deliberate?**

> Context: $0.01 means "I accept any price". If Base is a price-taker, the harness can assume award ≈ reported capability, so a policy only chooses *how much*, not *at what price*. Our scoring model already assumes this.

**4.4 ★ With ECRS at 97 of 100 MW and a 90% cap per QSE, are your AS volumes limited by the rules rather than by the fleet? Does that make real-time energy the main value driver?**

> Context: if the caps bind, a better capacity policy moves few dollars today, and the harness's value shifts to the "caps lifted" scenarios.

**4.5 About 60% of nameplate isn't in ADER groups. Is that registration lag, the caps, or utility-partner fleets? How is that part of the fleet monetized?**

> Context: this sets how big a fleet the harness should simulate.

**4.6 (Optional) How do your "103 of 145 MW", the 80.6 MW across your ADER groups, and ERCOT's 292.9 MW qualified reconcile?**

> Context: only a check on the public numbers. Skip it if time is short.

## 5. Dispatch and orchestration

**5.1 What decides the commitment each interval: an optimizer (MPC or stochastic program), a rules layer, or the real-time trading desk? What are the state variables? Your job postings describe dispatch as "sequential decision making", how human in the loop is this process?**

**5.2 How do you split an aggregate setpoint across thousands of homes: by SOC, by rotation, or by cost with degradation limits? How do you stay within about 500 cycles a year?**

> Context: SCED gives one target for a whole group, and Base must turn it into per-home commands. A cycle is one full discharge and recharge, and batteries wear with each one. The harness's coordinator does this same split.

**5.3 When a co-op partner (CoServ, GVEC) dispatches at the same time ERCOT prices spike, who has priority? How do you avoid committing the same kWh twice?**

> Context: co-ops are member-owned local utilities, and in Base's utility deals the utility gets dispatch rights. A co-op may want the battery for its own peak at the same moment Base wants it for ERCOT. The harness could model this as a second claim on the same SOC.

**5.4 What does Temporal do for you in BaseOS, and where did you choose not to use it?**

> Context: Temporal records each step of a process, retries failed steps, and resumes after a crash. The harness's run farm has the same crash-and-retry problem, so their answer helps us choose between Temporal and a Postgres queue, and justify the choice.

## 6. Harness fit (close with this)

**6.1 ★ How do you test a change to your capacity policy today before it goes live: backtest, shadow mode, simulation, trader review?**

> Context: this is the gap the harness fills. If they already have a mature simulator, aim at what it lacks: correlated failures, forward-looking scenarios, the run farm.

**6.2 ★ What does your policy take in and put out? Per-home or aggregated state, how often, and in what language? Could it sit behind a simple interface: state as JSON in, MW out?**

> Context: this decides the plug-in interface. Base writes Go and Python.

**6.3 ★ Which scenarios worry you that you can't test today? The fleet at 10x, caps lifting when the pilot becomes permanent rules, Non-Spin going to 2 h, RRS opening to ADERs, a hurricane over Houston during scarcity?**

**6.4 What would make you trust a harness's score: settlement matched to real invoices, reproducing a known bad day, something else? Is there a bad day, e.g. a CenterPoint storm or Winter Storm Fern (Jan 2026), when a regional event took out a large part of an ADER group while it carried reserves?**

> Context: a known bad day is the best first replay test. If the harness reproduces what really happened, its other scores become believable.

**6.5 Would a config-driven failure model (regions, rates, stress multipliers) that you could swap for real telemetry be useful, or is that already solved internally?**

**6.6 Is there public or shareable data we could use instead of our assumptions? The most useful would be dropout rates by cause, the share of the fleet dark on a normal day vs. a storm day, and deployment and shortfall history.**

> Context: the harness's placeholder assumptions today:
> - Home dropout during a deployment window: 1% calm, 4% storm.
> - Regional dropout: 0.5% calm, 8% storm, over 10 equal regions.
> - Fleet SOC averages about 60%.
> - Deployment chance per interval: 2% calm, 30% storm (to be estimated from public data).
> - Shortfall cost: real-time energy price × MW short (unverified, 3.1) plus $500/MW for compliance (placeholder, 3.2).
> - Award ≈ reported capability (4.3).
> - Backup floor fixed at 20% (1.5).
> - ECRS 1 h, Non-Spin 4 h (1.3, 1.4).
> - Dark homes count as zero, and telemetry older than 180 s is stale (2.3).

## Terms

- **AS / reserves:** ancillary services. ERCOT pays to have capacity standing ready, in $ per MW per hour.
- **ECRS:** a reserve product with 10-minute notice. It must be held for 1 h (or 2 h; the rules disagree).
- **Non-Spin:** a reserve product that must be held for 4 h (2 h after NPRR1309).
- **Deployment:** ERCOT calling on reserves it has awarded. It happens in scarcity.
- **SCED:** ERCOT's 5-minute dispatch. It sets each resource's MW target and awards reserves.
- **Set point / base point:** the MW target SCED sends every 5 minutes.
- **Load zone:** a price area of Texas (Houston, North, South, West). Retailers pay the real-time price for their zone.
- **ADER:** ERCOT's pilot for aggregated home devices. Base's battery groups are ADERs.
- **QSE:** the company that bids and settles with ERCOT. Base runs its own.
- **NPRR:** a formal request to change ERCOT's market rules.
- **Price-taker:** offers at about $0 and accepts any clearing price.
- **Headroom / haircut:** reporting less than the fleet has, as a buffer.
- **Cycle:** one full discharge and recharge.
- **Capability K:** MW the fleet reports it can provide. It's what a policy outputs.
- **BaseOS:** Base's internal platform that runs the fleet (Go, Python, Temporal, AWS).
- **Temporal:** open-source software that runs multi-step processes, retries failed steps, and resumes them after a crash.
