# ERCOT Ancillary Services after RTC+B — what "p" and "P" really are

Researched 2026-09-26. Primary sources are the current ERCOT Nodal Protocols (the versions posted on the ERCOT site, effective Aug 2026), ERCOT RTC+B settlement and task-force materials, market notices, the ADER Phase 3.3 governing document, and ERCOT data-product pages. Sources are numbered [n] at the bottom. Anything marked **unverified** did not come from a primary source.

Formula note: we extracted the protocol .docx files to plain text, which drops the Σ (summation) signs. Summing over resources and SCED intervals is implied where the protocol defines them.

---

## TL;DR

- **Since Dec 5, 2025, a Day-Ahead AS award is a financial position, not a physical promise.** In real time, SCED re-awards AS to specific resources every ~5 minutes, co-optimized with energy [3][6][12]. ERCOT staff: "AS awards from DAM are a financial obligation and not physical… AS awards are binding to the awarded Resource and not QSE (Settlement is QSE-level)." [6]
- **The old "Failure to Provide" charge is gone.** Protocol §6.7.3 "Charges for a Failure to Provide Ancillary Service" was deleted, along with the `*FQAMTQSETOT` charge types (e.g. `RUFQAMTQSETOT`) and the infeasible-AS charges (`*INFQAMT`) [2]. A two-settlement **AS Imbalance** replaced it (§6.7.2.1, charge types `RTRUIMBAMT`, `RTRDIMBAMT`, `RTRRIMBAMT`, `RTNSIMBAMT`, `RTECRIMBAMT`) [1][2][3].
- **The real "P" is the real-time AS clearing price (RT MCPC) for the same product, not the energy LMP.** Per product and 15-minute interval, the QSE is paid/charged `(RT AS awarded − DAM AS awarded − self-arranged + trades bought − trades sold) × RTMCPC / 4` [1]. The formula is **symmetric**: excess RT awards are paid at the same RT MCPC that shortfalls are charged at. There is no penalty multiplier [1].
- **Shortfall nets at QSE level.** RT revenue is summed over all resources of the QSE and compared with the QSE's total DAM position [1]. Any resource in the portfolio that SCED awards the product covers the position. The QSE can't manually reassign awards, though, because SCED picks the resources [6].
- **ESRs: SCED only awards AS the state of charge (SOC) can sustain.** SCED models ESR SOC and caps awards by the "SCED duration requirements" [1 §6.5.7.3, §6.4.9.1.1]. Current protocol durations: Reg-Up/Down 30 min, RRS 30 min, ECRS 1 h, Non-Spin 4 h (2 h once the DRRS change, NPRR1309, is implemented) [1 §8.1.1.3][4][6]. If SOC is too low, SCED simply awards less, and the uncovered DAM position is bought back at RT MCPC.
- **ASDCs replaced the ORDC adder.** Each product has a demand curve (ASDC), built by splitting an Aggregate ORDC. The Reg-Down curve is flat at VOLL (value of lost load). Floor is $15/MW-h over the AS Plan quantity. The VOLL used is the DAM high offer cap, $5,000 [1 §4.4.11, §4.4.12]. RT MCPCs are capped at the effective VOLL [1 §6.5.7.3].
- **ADER pilot (Phase 3.3, current):** aggregations of home devices (≤1 MW each premise, ≥100 kW total) join as Aggregate Load Resources (ALR, SCED-dispatchable) or Aggregate Non-Controllable Load Resources (NCLR, not SCED-dispatched). They can sell **energy (ALR only), Non-Spin and ECRS only**: no Reg, and RRS only "considered". Caps: 500 MW registered, 100 MW Non-Spin, 100 MW ECRS system-wide. One QSE may hold at most 90% of each cap. 2-second telemetry, 15-minute premise interval meters. ADERs settle under the same AS Imbalance rules [7][8][9].

---

## 1. AS products after RTC+B

RTC+B went live for Operating Day Dec 5, 2025 [12][13]. Products and rules come from Protocol §3.17, §3.18, §8.1.1.3 [1].

| Product | Purpose | Deployment | ESR sustain requirement (current protocols) | Who can provide |
|---|---|---|---|---|
| **Reg-Up / Reg-Down** | Moment-to-moment frequency balancing | Load Frequency Control (LFC) signal; respond within **5 s** [1 §3.17.1] | Must be sustainable **≥30 min** [1 §8.1.1.3.1(2)] | Generation Resources, ESRs (charge or discharge mode), Load Resources [1 §3.17.1] |
| **RRS** | Arrest and restore frequency after a large trip | Self-deployed automatically [1 §3.17.2] | SCED-dispatchable resources **≥30 min**; others ≥1 h; FFR (fast frequency response) **15 min** [1 §8.1.1.3.2(4)] | Sub-types: PFR (primary frequency response) from online generation; **FFR**; Load Resources on high-set under-frequency relays; Controllable Load Resources (CLRs); synchronous condensers; **ESRs** [1 §3.17.2(3)] |
| **ECRS** | Back up RRS and Reg; supply energy before or during emergencies (EEA) | **10-minute** notice; under RTC, "deployment" happens when SCED runs short of ECRS [1 §3.17.4][6] | **≥1 h** per §8.1.1.3.4(2). §3.17.4(1) still says "two consecutive hours"; this inconsistency is in the posted protocols [1] | Online generation, Quick Start Generation Resources (QSGRs), Load Resources, CLRs, synchronous condensers, **ESRs**. ESR ECRS ≤ 10 × emergency ramp rate [1 §3.17.4(4), §3.18(4)] |
| **Non-Spin** | Cover multi-hour forecast error or outages | Within **30 min** [1 §3.17.3] | **≥4 consecutive h**; becomes **2 h** when NPRR1309 is implemented [1 §8.1.1.3.3(2)] | Online/offline generation, CLRs, non-CLR Load Resources, **ESRs** [1 §3.17.3] |
| DRRS (new) | Multi-hour reserve to reduce RUC (Reliability Unit Commitment) | 2-hour ramp, 4-hour run | n/a | **Generation Resources only** [1 §3.17.5]. NPRR1309 was approved by PUCT on 2026-07-09; not yet implemented [10] |

- The durations above match ERCOT staff's March 2025 RTC task-force recommendation (Reg/RRS 30 min, ECRS 1 h, Non-Spin 4 h; RUC uses 1 h for all) [6].
- Under RTC, SCED enforces these durations against telemetered SOC [1 §6.5.7.3(1)]. So, per MW of each award, an ESR must hold roughly 0.5 MWh (Reg, RRS), 1 MWh (ECRS), or 4 MWh (Non-Spin), and all simultaneous awards together. **Unverified** that the constraint is exactly additive across products; see Modo [S1].
- The sum of AS awards plus LSL must be ≤ HSL for each resource [1 §3.18(1)].

## 2. Procurement, MCPCs, ASDCs

**DAM (day-ahead market).**
- Offers are due by 10:00 the day before [1 §4.4.7.2].
- Two offer types:
  - resource-specific AS offers, co-optimized with energy;
  - **AS-Only Offers**, a new QSE-level offer not tied to any resource [1 §4.4.7.2(4), §4.4.7.2.3].
- DAM clears against the ASDCs [1 §4.4.7.2(9), §4.5.1(4)].
- Payment: `PCRUAMT_q = (−1)·MCPCRU_DAM·PCRU_q` for resource-specific awards and `DAPCRUOAMT_q = (−1)·MCPCRU_DAM·DARUOAWD_q` for AS-Only awards. Other products are analogous [1 §4.6.4.1.1]. Negative means a payment to the QSE.
- AS-Only offers are capped at DASWCAP ($5,000) and floored at $0 [1 §4.4.7.2.3].

**Real time.**
- SCED awards AS to each resource every run. Awards and RT MCPCs are "immediately binding upon the completion of a SCED run" [1 §6.4.9.1.1(8)].
- Awards depend on qualification, limits, ramp, **SOC and SOC limits**, and the ASDCs, "regardless of the quantity of Ancillary Service under deployment" [1 §6.4.9.1.1(1)].
- QSEs must submit RT AS offers and resource-level AS telemetry [1 §6.4.9.1.1(2)].
- There is no more HASL (High Ancillary Service Limit) and no QSE-level assignment of AS to resources [6].

**Settling DAM against RT (the two-settlement).** §6.7.2.1–6.7.2.6 [1]. Reg-Up shown; other products are identical, with prefixes RD, RR, NS, ECR.

```
RTRUIMBAMT_q = (−1) · [ Σ_r RTRUREV_q,r
                        − Σ_r ¼·PCRUR_r,q,DAM · RTMCPCRU
                        − ¼·DASARUQ_q · RTMCPCRU
                        + ¼·(RUTP_q − RUTS_q) · RTMCPCRU ]
RTRUREV_q,r = ¼ · RTRUAWD_q,r · RTMCPCRUR_q,r
```

- `RTRUAWD` is the time-weighted RT award.
- `RTMCPCRUR` is the RT MCPC for that resource, weighted by its awards across SCED intervals. It includes the Real-Time Reliability Deployment Price Adder, `RTRDPARUS`.
- `RTMCPCRU` is the 15-minute RT MCPC.
- The charge types are new with RTC+B [2][3]:
  - `RTRUOAMT = ¼·DARUOAWD·RTMCPCRU`: AS-Only DAM awards are **always bought back at RT MCPC**, because they have no physical resource behind them;
  - `RTRUTOAMT`: trade-overage charge;
  - `LARTRUAMT` etc.: charges allocated to load.

**ASDCs** (§4.4.12 [1]).
- An Aggregate ORDC is fitted by regression to 2014-06 → 2025-08 reserve pricing data (updated for Summer 2026), then split into ASDCs for Reg-Up, RRS, ECRS and Non-Spin, up to the Minimum Contingency Level.
- Reg-Down ASDC = constant **VOLL** over its AS Plan quantity.
- All ASDCs have a floor of **$15/MW-h** for the AS Plan portion; DRRS has a $10 floor once implemented.
- DAM and RT use the same ASDCs; the DAM curves are adjusted for negative self-arranged quantities.
- ERCOT posts them as report NP4-212-CD [16].

**Caps** (§4.4.11 [1]).
- VOLL for the ASDCs = HCAP-DAM = **$5,000/MWh**. RTSWCAP (RT offer cap) = $2,000. ECAP and LCAP = $2,000.
- If Peaker Net Margin passes $315k/MW-yr, VOLL drops to LCAP for the rest of the year. The Emergency Pricing Program (EPP) sets it to ECAP.
- RT MCPCs from SCED Step 2 "shall also be capped at the effective VOLL" [1 §6.5.7.3].

## 3. Failure to provide: what the QSE actually pays (the real "P")

1. **No separate failure-to-provide penalty exists any more.** RTC+B deleted §6.7.2 (AS capacity assigned in RT), §6.7.2.1 (infeasible AS due to transmission), §6.7.3 (Failure to Provide), and the old RT AS imbalance. The removed charge types include `RUFQAMTQSETOT … ECRFQAMTQSETOT` (failed quantity) and `RUINFQAMT … ECRINFQAMT` (infeasible) [2]. The pre-RTC rule charged failed quantity × max(DAM, SASM MCPC), per ERCOT's archived PUCT-directive page. That rule no longer applies (see [S3], secondary summary of pre-RTC protocols).
2. **The shortfall is settled by the AS Imbalance.** If a resource can't carry its DAM quantity (SOC, outage, telemetry derate, or SCED simply choosing a cheaper supplier), SCED awards it less. The QSE pays `(DAM position − Σ RT awards) × RTMCPC/4` for each 15-minute interval [1 §6.7.2.2]. **P = the RT MCPC of that same product** (15-minute, including reliability-deployment adders). It does not depend on the energy price.
3. **Netting is QSE-level, across resources.** The formula sums RT revenue over all of the QSE's resources and subtracts the QSE's total DAM awards [1]. Awards are resource-specific and chosen by SCED [6]. A QSE influences which of its resources carry AS only through offers and telemetry. It cannot reassign awards directly.
4. **QSE-to-QSE AS Trades** move positions. Settlement adds bought and subtracts sold quantities [1 §4.4.7.3, §6.7.2.1]. Net purchases above self-arranged plus DAM awards incur a trade-overage charge at RT MCPC [1 §4.4.7.1(7)].
5. **ERCOT derates.** If ERCOT manually cuts a resource's AS eligibility (e.g., for transmission), the QSE can file for a Derated AS Capability Payment. It is capped at RT MCPC × reduced MW (`RTDASAMT`) and allocated to load (`LARTDASAMT`) [1 §6.4.9.1.1(6), §6.7.2.7–6.7.2.8].
6. **Non-price consequences.**
   - Monthly AS capacity compliance (the S%/T%/U-MW criteria) is reported to the Reliability Monitor [1 §8.1.1.3(2)–(4)].
   - Deployment performance is measured for Regulation, including ESR energy-deployment tolerance [1 §8.1.1.4.1].
   - ERCOT may run unannounced ECRS/Non-Spin SOC capability tests on ESRs [1 §8, para (19)].
   - Energy deviations from base points are charged as Set Point Deviation. The ESR tolerance is the lesser of 3% or 3 MW [1 §6.6.5.5–6.6.5.5.1][2].
   - For ADERs, ERCOT can revoke Non-Spin/ECRS qualification after "a continuing failure to perform" [8].

## 4. ESR state of charge under RTC+B

- **Telemetry.** Each ESR sends real-time MaxSOC, MinSOC and SOC (MWh) plus max discharge and charge power (MW). The QSE must keep MinSOC ≤ SOC ≤ MaxSOC [1 §6.5.5.2(14)–(15)].
- **COP.** The COP (Current Operating Plan) carries an hour-beginning planned SOC, and consecutive hours must be feasible at max charge/discharge rates [1 §3.9.1(3)]. Hour-beginning SOC also feeds RUC [2] (NPRR1236).
- **SCED.** SCED "accounts for each ESR's SOC and SOC operating limits" so that base points and AS awards are feasible given the SCED duration requirements and MinSOC/MaxSOC [1 §6.5.7.3(1)].
- **Posted capability.** The published AS capability for ESRs is "further capped by … SCED duration requirements and current available SOC" [1 §6.5.7.5(6)]; report NP6-328-CD [15].
- **Insufficient SOC:** SCED does not award (or reduces) AS on that ESR. There is no penalty beyond buying back any DAM position at RT MCPC. The QSE may also lower telemetered HSL/LSL to reflect SOC [1 §3.8.5(1)].
- **Model implication:** in RT, availability A is effectively set by ERCOT from SOC/duration, not by the fleet choosing to under-deliver.

## 5. ADER pilot

- **History** [7][9]:
  - Phase 1 approved 2022-10-18; participation began 2023-08-22.
  - Phase 2 approved 2024-02-27 and added ECRS.
  - Phase 3 approved 2025-06-23/24 and added the NCLR model, including third-party QSE aggregation of premises >100 kW.
  - Phase 3.2 (Feb 2026) raised the caps.
  - Phase 3.3 approved at the June 1–2, 2026 Board meeting; it is current.
  - After at least six months of Phase 3, ERCOT is to draft an NPRR moving ADER into the Protocols [7]. A status update, "ADER Transition from Pilot to Protocols", was given in April 2026 (**unverified**: search-snippet only).
- **Caps.**
  - Phases 1–2: 80 MW energy, 40 MW Non-Spin, 40 MW ECRS [7].
  - Phase 3 at launch: 160 / 80 / 80 [7].
  - Market notice M-A030226-01 (2026-03-02): registered capacity 200 → **500 MW**, AS limit **100 MW**, QSE limit 50% → **90%** [9].
  - Phase 3.3 text: 500 MW total, ≤100 MW Non-Spin and ≤100 MW ECRS system-wide, no QSE >90% [8].
- **Services** [8][11]:
  - **ALR model:** energy via SCED at the Load Zone price, plus Non-Spin and ECRS. Must be SCED-qualified.
  - **NCLR model:** Non-Spin and ECRS only, deployed by XML instruction through the AS Deployment Manager, no SCED energy.
  - RRS is only "considered" and subject to a cap; PFR is optional. No Regulation.
  - ERCOT's CRA consultant report (Apr 2026 Board item) confirms "ECRS and Non-Spin, but not RRS" [11].
- **Eligibility.**
  - Each premise ≤1 MW of response; aggregation ≥100 kW; all premises in one Load Zone and one DSP.
  - ALR model: same LSE required. NCLR model: premises >100 kW can have different LSEs, with the LSE's QSE acknowledgment.
  - The aggregate must always telemeter as net load, using an ERCOT-assigned MW offset; net injection counts as demand response [8].
- **Telemetry and metering** [8].
  - Resource-level telemetry every **2 s** to ERCOT.
  - 15-minute premise interval meters (AMS/IDR) are the validation basis.
  - Device-level telemetry requires sub-meter data on request: 1-minute data for ALR, 5-minute for NCLR [7].
  - Battery SOC time series is required for validation.
  - Telemetry validation criteria: within 10% of meter data (50% when ≤1 MW for device-level), ≥50% of intervals over an 8-hour window.
  - No statistical sampling. NCLR performance is measured by meter-before/meter-after baseline.
- **Settlement.** ADERs "will be subject to … the Ancillary Service Imbalance Settlement calculations" [8]. The same P = RT MCPC applies.
- **Scale.**
  - May 2025: 3 qualified ADERs; 38.3 MW energy, 11 MW Non-Spin, 8.8 MW ECRS (qualified plus pending) [7].
  - Feb 2026: 13 registered, 7 active (**unverified**: search snippet of [11], text truncated in our extract).
  - ADERs were cleared for Non-Spin in 52–63% of RT intervals from Nov 2024 to Feb 2025 [11].

## 6. Public data for a backtest

| Data | ERCOT report (EMIL) | Notes | gridstatus |
|---|---|---|---|
| DAM MCPC by product, hourly | **NP4-188-CD**; history **NP4-181-ER** [14] | Since 2010 | `Ercot().get_as_prices`, `get_mcpc_dam`; `ErcotAPI().get_as_prices` (public API, historical) [17] |
| RT MCPC per SCED interval | **NP6-332-CD** (24 h); **NP6-795-ER** weekly history, from 2025-12-05 [14] | 5-min | `Ercot().get_mcpc_sced` [17] |
| RT MCPC per 15 min (the settlement price, incl. adders) | **NP6-331-CD** (7-day display), from 2025-12-05 [14] | Time-weighted MCPC + RT reliability-deployment price adder | `Ercot().get_mcpc_real_time_15_min` [17] |
| Indicative RT MCPC (RTD) | NP6-329-CD | | `get_indicative_mcpc_rtd` [17] |
| ASDCs (DAM and SCED) | **NP4-212-CD** [16] | Hourly curves | `get_as_demand_curves_dam_and_sced` [17] |
| AS capability incl. ESR SOC cap | **NP6-328-CD** [15] | After every SCED | `get_as_total_capability` [17] |
| AS offers / awards, DAM | NP4-179-CD (total offers); NP4-532-CD (DAM total AS sold) | | `get_as_reports_dam`, `get_dam_total_as_sold` [17] |
| Resource-level awards, SOC, AS offers | 60-Day DAM **NP3-966-ER**, 60-Day SCED **NP3-965-ER** | Includes ESR data | `get_60_day_dam_disclosure`, `get_60_day_sced_disclosure` [17] |
| Pre-RTC+B history | Same DAM reports; pre-Dec-2025 RT had no RT MCPC (SASM only) | Structural break at 2025-12-05 | — |

Caveat: gridstatus's `Ercot()` RT MCPC methods read ERCOT MIS documents, which only keep a few days. For a longer history, use NP6-795-ER weekly files or gridstatus.io hosted datasets. That gridstatus.io hosts full history is **unverified**. gridstatus code notes an `UncappedMCPC` column "only published from 2026-08-27 onward" [17]. We did not find the protocol change behind it; **unverified**.

## 7. Implications for our model

1. **Replace P with the RT MCPC of the same product, not the energy LMP.** Per hour: `profit = p_DA·C + Σ_15min p_RT·(A_t − C)/4`, with A_t = RT AS awarded to the fleet, not physical availability [1 §6.7.2.2].
2. **Make it symmetric.** If A > C, the fleet is *paid* p_RT on the excess. The `max(C − A, 0)` term becomes the linear `(A − C)`. The only asymmetry is that A ≤ the SOC/duration-limited capability.
3. **C is a financial DAM position (a forward), not a physical promise.** The decision becomes a bet on the DA–RT MCPC spread plus RT availability. AS-Only Offers even let a QSE sell DAM AS with no resource at all, always bought back at RT [1 §6.7.2.2(2)].
4. **Availability A is set by SCED from SOC, not chosen by the fleet.** Cap it at `SOC_available / duration_h` per product (0.5 h Reg/RRS, 1 h ECRS, 4 h Non-Spin today), and share it across products and energy [1 §8.1.1.3][6].
5. **Net shortfall across the whole QSE portfolio.** Model aggregated fleet A, not per-battery A [1].
6. **Watch the price correlation.** The ASDCs tie p_RT to system reserve scarcity. The high-P events are exactly when home batteries are most likely discharging for energy or SOC-constrained. Model p_RT and energy price jointly; scarcity drives both.
7. **For a VPP/ADER, the product set is Non-Spin and ECRS only**, within the 100 MW caps, and the ADER is a Load Resource, not an ESR [8]. The ESR SCED-SOC logic does not directly apply to an ADER. ADER A comes from QSE telemetry (MPC/LPC) and is validated against meters. The CLR Non-Spin rule is to hold consumption level ≥4 h [1 §3.17.3(1)(b)].
8. **Add small performance and compliance risk** (qualification revocation, compliance reports, deviation charges), but not a $/MW failure penalty.
9. **Fix the price units.** MCPC is in $/MW per hour; settlement is per 15 minutes with a ¼ factor. The cap is VOLL ($5,000/MW-h) [1 §4.4.11].

## Open questions for a Base engineer

1. Does Base participate as an ADER (ALR or NCLR?), as a DESR/ESR, or not in AS at all? Which QSE represents it?
2. Does Base sell AS in the DAM, only in RT, or through AS-Only/self-arrangement? How does its bidding handle the DA–RT MCPC spread?
3. How does Base compute its telemetered AS capability from fleet SOC? How much SOC headroom does it hold back for customer backup?
4. Are home-battery AS awards netted with other resources in Base's QSE portfolio (e.g., retail load and self-arranged obligations as an LSE)?
5. Has Base seen ADER qualification or telemetry-validation failures, or Non-Spin/ECRS deployment performance issues?
6. How often do ECRS/Non-Spin deployments (SCED going short) actually drain the fleet? How are customers compensated?
7. Is the ECRS duration Base plans for 1 h or 2 h (given the §3.17.4 vs §8.1.1.3.4 discrepancy), and does it expect Non-Spin to move to 2 h under NPRR1309?

---

## Sources

Primary:
1. ERCOT Nodal Protocols, current: Sections 3, 4, 6, 8 (effective Aug 2026). Index https://www.ercot.com/mktrules/nprotocols/current ; §3 https://www.ercot.com/files/docs/2025/09/01/03-080126_Nodal.docx ; §4 https://www.ercot.com/files/docs/2024/06/28/04-080126_Nodal.docx ; §6 https://www.ercot.com/files/docs/2024/06/28/06-082826_Nodal.docx ; §8 https://www.ercot.com/files/docs/2022/12/09/08-080126_Nodal.docx
2. ERCOT, "RTC+B Settlement Overview", M. Shanks, RTCBTF (removed and added charge types; NPRR list) — https://www.ercot.com/files/docs/2025/07/09/RTC-B-Settlement-Overview.pdf
3. ERCOT Market Notice M-C110525-01, RTC+B settlement implementation details — https://www.ercot.com/services/comm/mkt_notices/M-C110525-01
4. See [1] §8.1.1.3.1–8.1.1.3.5 (duration requirements).
5. NPRR1013 (RTC NP 1/2/16/25; effective 2025-12-05) — https://www.ercot.com/mktrules/issues/NPRR1013
6. ERCOT staff, "AS Duration Under RTC", RTCTF, 2025-03-25 — https://www.ercot.com/files/docs/2025/03/20/RTCTF_Duration_Requirements_Topic_Update_v6.pptx
7. ERCOT Board Item 4.3, ADER Pilot Phase 3 (June 2025) — https://www.ercot.com/files/docs/2025/06/16/4.3-Aggregate-Distributed-Energy-Resource-ADER-Pilot-Project-Phase-3.pdf
8. ADER Pilot Governing Document Phase 3.3 — https://www.ercot.com/files/docs/2026/03/02/ADER-Pilot-Project-Governing-Document-Phase-3.3.docx ; pilot page https://www.ercot.com/mktrules/pilots/ader
9. Market Notice M-A030226-01, ADER participation limits — https://www.ercot.com/services/comm/mkt_notices/M-A030226-01
10. NPRR1309 DRRS — https://www.ercot.com/mktrules/issues/NPRR1309
11. ERCOT Board Item 11.1 (Apr 2026) incl. CRA demand-response report — https://www.ercot.com/files/docs/2026/04/13/11.1-Strategic-Discussion-on-Resource-Adequacy-and-the-Role-of-Demand-Response.pdf
12. ERCOT news release, RTC+B go-live 2025-12-05 — https://www.ercot.com/news/release/12052025-ercot-goes-live
13. Market Notice M-F110525-04, go-live complete — https://www.ercot.com/services/comm/mkt_notices/M-F110525-04
14. ERCOT data products: NP6-331-CD https://www.ercot.com/mp/data-products/data-product-details?id=NP6-331-CD ; NP6-332-CD https://www.ercot.com/mp/data-products/data-product-details?id=NP6-332-CD ; NP6-795-ER https://www.ercot.com/mp/data-products/data-product-details?id=NP6-795-ER ; NP4-188-CD https://www.ercot.com/mp/data-products/data-product-details?id=NP4-188-CD ; NP4-181-ER https://www.ercot.com/mp/data-products/data-product-details?id=NP4-181-ER
15. NP6-328-CD AS capability — https://www.ercot.com/mp/data-products/data-product-details?id=NP6-328-CD
16. NP4-212-CD ASDCs — https://www.ercot.com/mp/data-products/data-product-details?id=np4-212-cd
17. gridstatus source (first-party for the library's capabilities) — https://github.com/gridstatus/gridstatus/blob/main/gridstatus/ercot.py , https://github.com/gridstatus/gridstatus/blob/main/gridstatus/ercot_api/ercot_api.py

Secondary (used only where marked unverified or for context):
- S1. Modo Energy, RTC+B AS duration and SOC — https://modoenergy.com/research/en/rtcb-real-time-cooptimization-rtc-ercot-ancillary-service-duration-soc-management
- S2. Grid Status blog, "RTC+B, 60 Days Later" — https://blog.gridstatus.io/rtc-b-60-days-later-in-ercot/
- S3. ERCOT PUCT-directive page "Ancillary Service Imbalance Settlement with RTC" (file listing only) — https://www.ercot.com/mktrules/puctDirectives/kp1p6 ; the pre-RTC failure-to-provide pricing text came from a search-engine summary of ERCOT market notice archives.
