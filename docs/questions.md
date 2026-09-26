# Questions for a Base engineer

*Read top to bottom. We're positioned as a **test harness for capacity policies**. The ★ questions shape what the harness must model and how it plugs in; ask those first if time is short. The context lines are for you, not for reading aloud.*

## Start here: what isn't solved yet (★)

**A ★ What's something that isn't mature yet, or that you wish you had time to build?**

> Context: a sanity check on the harness positioning. If they name something closer to evaluation tooling or scenario testing, lean into it.

**B ★ Is Distributed Compute live? When there's a grid event, how does the compute on the battery fleet back off, and how fast?**

> Context: a job posting mentions GPU sites on the battery fleet, with a dispatch layer that decides when compute runs or backs off. Parked as a direction (too many assumptions to build on), but worth hearing about. Only one job posting backs this, so confirm it's real.

## 0. Opener (★)

**0.1 ★ Since RTC+B went live in Dec 2025, where does Base actually lose money or sleep: day-ahead vs. real-time AS imbalance, under-delivery when ERCOT deploys, telemetry validation, or something else?**

> Context: RTC+B deleted Failure-to-Provide. Day-ahead AS is now bought back symmetrically at the real-time clearing price, so we think the real risk sits in real-time capability and deployment. The answer decides what the harness's scoring model must get right.

## 1. How capability gets reported

**1.1 ★ How do you turn fleet state into the capability number you report to ERCOT for ECRS and Non-Spin: SOC, heartbeats, homes in backup mode? Where is the capability decided/computed and what algorithms/decisions are involved in that process? how does it work ms by ms and second by second?**

> Context: ADERs report capability every 2 s. It's the number SCED awards against.

**1.2 ★ Do you haircut that number? Is the haircut fixed, or does it depend on weather, time of day, region, or price? Roughly how big is it today?**

**1.3 Do you size ECRS at 1 hour or 2 hours of energy per MW?**

> Context: the protocols contradict each other: §8.1.1.3.4 says 1 h, §3.17.4 says 2 h.

**1.4 Do you plan for Non-Spin moving from 4 h to 2 h under NPRR1309 (DRRS)?**

> Context: Non-Spin is energy-limited for a 39.2 kWh battery: about 4 kW per home at 60% SOC.

**1.5 The times when high SOC is needed are also the times when pricing surges and its most lucrative to promise capacity to ERCOT, how does the minimum charge you promise customers factor into the ERCOT capacity promises? How does the 20% SOC floor enter the AS duration math? Is the floor fixed, or dynamic with outage forecasts, e.g. pre-charging before storms?**

## 2. Correlated failures

**2.1 ★ Do you model availability loss as correlated (feeder, substation, cell carrier, firmware cohort, storm path) or as independent per device?**

**2.2 When a region loses power or connectivity, how fast does that reach the reported capability? Every 2 s, or on a slower loop?**

**2.3 What do you assume about a device that goes dark: last known state, zero, or a statistical estimate? What share of the fleet is dark on a normal day vs. during a storm?**

> Context: your blog treats telemetry older than 180 s as stale.

**2.4 Has a regional event (a CenterPoint storm, Winter Storm Fern in Jan 2026) ever taken out a large part of an ADER group while it was carrying AS? What happened?**

## 3. Deployment and penalties

**3.1 ★ When an ECRS or Non-Spin deployment under-delivers, what exactly happens financially? Energy imbalance at the load-zone price, a base-point deviation charge, or nothing beyond the AS imbalance?**

> Context: I couldn't verify this from the protocols for ADER Load Resources. It's the "P" in our model.

**3.2 ★ How real is the compliance risk: telemetry validation (within 10% in at least 50% of intervals) and revocation of qualification for "continuing failure to perform"? Has Base had a close call?**

**3.3 How often do deployments (SCED running short of ECRS or Non-Spin) actually drain the fleet? How are members affected or compensated?**

**3.4 Can you share any history of under-delivery: how often, the root causes, and did it ever change your policy?**

## 4. Market structure and strategy

**4.1 Are your ADER groups registered as ALR (SCED-dispatched, sells energy) or NCLR (AS only, deployed by instruction)?**

**4.2 Do you sell AS day-ahead, or carry it mainly through real-time co-optimization awards?**

**4.3 ERCOT's report shows ADER AS offers at about $0.01. Is that Base, and is price-taking deliberate?**

**4.4 ★ With ECRS at 97 of 100 MW and a 90% cap per QSE, are your AS volumes limited by the rules rather than by the fleet? Does that make real-time energy the main value driver?**

**4.5 About 60% of nameplate isn't in ADER groups. Is that registration lag, the caps, or utility-partner fleets? How is that part of the fleet monetized?**

**4.6 How do your "103 of 145 MW", the 80.6 MW across your ADER groups, and ERCOT's 292.9 MW qualified reconcile?**

## 5. Dispatch and orchestration

**5.1 What decides the commitment each interval: an optimizer (MPC or stochastic program), a rules layer, or the real-time trading desk? What are the state variables? Y**our job postings describe dispatch as "sequential decision making", how human in the loop is this process?

**5.2 How do you split an aggregate setpoint across thousands of homes: by SOC, by rotation, or by cost with degradation limits? How do you stay within about 500 cycles a year?**

**5.3 When a co-op partner (CoServ, GVEC) dispatches at the same time ERCOT prices spike, who has priority? How do you avoid committing the same kWh twice?**

**5.4 What does Temporal do for you in BaseOS, and where did you choose not to use it?**

## 6. Harness fit (close with this)

**6.1 ★ How do you test a change to your capacity policy today before it goes live: backtest, shadow mode, simulation, trader review?**

> Context: this is the gap the harness fills. If they already have a mature simulator, aim at what it lacks: correlated failures, forward-looking scenarios, the run farm.

**6.2 ★ What does your policy take in and put out? Per-home or aggregated state, how often, and in what language? Could it sit behind a simple interface: state as JSON in, MW out?**

> Context: this decides the plug-in interface. Base writes Go and Python.

**6.3 ★ Which scenarios worry you that you can't test today? The fleet at 10x, caps lifting when the pilot becomes permanent rules, Non-Spin going to 2 h, RRS opening to ADERs, a hurricane over Houston during scarcity?**

**6.4 What would make you trust a harness's score: settlement matched to real invoices, reproducing a known bad day, something else?**

**6.5 Would a config-driven failure model (regions, rates, stress multipliers) that you could swap for real telemetry be useful, or is that already solved internally?**

**6.6 Is there public or shareable data (anonymized dropout rates, deployment history) that we could use instead of assumptions?**