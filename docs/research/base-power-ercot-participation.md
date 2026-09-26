# Base Power: ERCOT participation, business model, and how it promises capacity

Researched 2026-09-26. **[P]** means a primary source (Base, ERCOT, PUCT, Base job postings, utility press releases). **[S]** means secondary (news or aggregators). **[I]** means my own inference from the sources, not a stated fact. Anything marked "unverified" has no primary source I could find.

## TL;DR

- **Legal structure.** There are four entities. Base Power, Inc. is the parent. **Base Texas REP, LLC** is the Texas Retail Electric Provider, PUCT Certificate **#10338**. **Base Texas QSE, LLC** is Base's own ERCOT QSE; it filed its ADER registration in PUCT Project 54311 on 2025-11-03. **Base Power Development, LLC** owns the batteries and holds the 12-year Battery Service Agreement with each member. In Illinois, **Base Retail, LLC** is an ARES under ICC license #26-0121 (ComEd territory, PJM). [P]
- **Market position.** Base calls itself the largest ADER pilot participant: "103 of 145 total MW registered", or 71%. In March 2026 ERCOT raised the per-QSE cap from 50% to **90%** of system-wide limits. The pilot's AS caps are 100 MW Non-Spin and 100 MW ECRS system-wide. ECRS was 97.3 MW qualified in Aug 2026, so it is essentially full. [P]
- **Services.** ADERs are dispatched by SCED every 5 minutes as Aggregate Load Resources and settled at the **load-zone price**. They can carry **Non-Spin and ECRS**. RRS is only "under consideration", and I found no Reg-Up/Reg-Down path for ADERs. [P]
- **Fleet size.**
  - 205.5 MW nameplate discharge in Jul/Aug 2026, excluding partner-utility fleets. Only **39% (80.6 MW)** of that is enrolled in ADER partitions (North 22.9, South 7.2, Houston 50.5, plus 30 MW Houston pending). [P]
  - More than 500 MWh installed by Aug 2026, at about 100 batteries/day (about 2 MW/day). [P/S]
  - "30,000+ homeowners" across TX and IL. [P]
- **Backup vs grid.**
  - Base "aims to reserve at least 20%" state of charge (SOC). Its data "shows that batteries never drop below 20%, and it's rare that they ever even fall below 50%". [P]
  - The help center promises at least 5 h (1 unit) or 10 h (2 units) of low-usage backup, sized for 97.5% of Texas outages. [P]
  - In an outage the battery islands the home and "will never discharge to the grid". [P]
  - Base has "outage prediction technology" that shapes charging. [P]
- **Hardware.** Base Core is **20 kW / 39.2 kWh**, or 78.4 kWh with two units. It is LFP chemistry and switches to backup in 50 ms. The older ground-mounted units are 25 or 50 kWh with an **11 kW** inverter. [P]
- **Software and ops.**
  - Internally the platform is called **BaseOS**, written in Go and Python. It uses Temporal workflows, AWS and Terraform. [P]
  - Devices talk to the cloud over MQTT. Base connects to ERCOT via ICCP over a private backhaul, with a 24/7 on-call desk. [P]
  - Telemetry older than 180 s is treated as stale. [P]
  - A "Markets" team runs a real-time trading desk with rotations. It treats dispatch as a "sequential decision making problem" (MPC, RL, MDPs) and hedges with financial contracts. [P]

---

## 1. Corporate and market structure

| Item | Finding | Source |
|---|---|---|
| Is Base a REP? | Yes. Base Texas REP, LLC, "certified REP ... under PUCT Certificate No. 10338" | [P] Energy Terms of Service v20260601 [1]; site footer [2] |
| QSE | **Base Texas QSE, LLC**. Delaware LLC created 2023-12-06, LEI issued 2024-11-21, office at 205 E Riverside Dr, Austin. It filed "Confidential Submission of ADER Registration" in PUCT Project 54311 on 2025-11-03. Base's ADER connectivity diagram shows a "Base QSE" linked to ERCOT over ICCP. | [P] PUCT 54311 filings list [3]; [P] Base ADER blog [4]; [S] LEI record [5]; [S] EnergyChoiceMatters 2023-12-29 [6] |
| QSE level | The diagram labels the link "QSE4". **[I]** This is probably ERCOT's QSE qualification level for QSEs representing Resources that provide AS. Unverified. | [P] [4] |
| Battery owner | Base Power Development, LLC, under a 12-year Battery Service Agreement. Base retains ownership, and members cannot override the charge/discharge schedule. | [P] ToS [1]; help center [7] |
| Other entities | Base Retail, LLC holds Illinois ARES license #26-0121. In Colorado and Connecticut, Base sells equipment and installation only; Base Power Development holds CT HIC.0707034. | [P] site footer [2] |
| Texas retail service areas | Oncor, CenterPoint, AEP Texas Central, AEP Texas North, TNMP. The ToS also lists Lubbock Power & Light among the outage contacts. | [P] pricing page [8]; ToS [1] |
| Utility/co-op "backup" areas (non-REP) | GVEC, CoServ, Farmers EC, Bandera EC, Austin Energy, El Paso Electric (EPE is non-ERCOT) | [P] pricing page [8]; help center partnerships [7] |
| Illinois | ComEd (PJM) | [P] [2], [8] |

## 2. Business model

**Retail plus battery bundle (Texas competitive areas)**
- Oncor "Energy + Backup": $695 one-time install, **$19/mo** membership, all-in about **13.9¢/kWh** (7.7¢ energy plus delivery), with the rate fixed. [P] /core [9]
- A Houston example quoted by TechCrunch: $695, $19/mo, 13.1¢/kWh. [S] [10]
- Energy-only plans run about 14.3¢/kWh, fixed for 36 months. [P] /energy [11]
- **Battery Charging Credit:** Base credits the member for grid energy used to charge the battery, at energy rate plus TDU delivery. It measures that energy from SMT interval data and/or **battery telemetry**. The member therefore does not pay for arbitrage charging. [P] ToS [1]
- Solar buyback is 4¢/kWh. [P] /specs/core [12]

**Stated monetization**
- The pitch is "Base earns money from the grid, not from you". Base describes three reasons it discharges: to sustain the business, to save members money, and to support the grid. [P] blog [13]; help [7]
- Real-time energy arbitrage and ancillary services in ERCOT: the Algorithms Engineer and Quant Dev postings name "realtime energy arbitrage and ancillary services in wholesale energy markets (e.g. ERCOT)". [P] [14][15]
- Hedging: Base "enters into financial contracts to hedge against volatility in electricity markets". [P] Quant Dev Intern posting [15]
- Utility deals: Base either tolls Base-owned batteries to a utility "on a pay-for-performance basis" or uses **build-transfer**, where ownership moves to the utility. In both, the utility gets full dispatch rights. Products offered are bulk peaking capacity/resource adequacy, speed-to-power for large loads, and distribution grid support. [P] /utilities [16]
- Transmission and large-load value: the ADER Phase IV (nodal aggregation) pitch is that about 80 MW of targeted batteries relieves the constraints created by a 100 MW prospective load at Burleson Switch. That figure comes from a Piq Energy study (Aug 2026) that Base cites. [P] [4]
- New line, **Distributed Compute**: modular GPU datacenters on battery-fleet sites. The dispatch layer "decides — together with our energy planning systems — when compute runs, throttles, or drains". [P] Server Architect and Network Engineer postings [17]

**Backup guarantee and reserved SOC**
- "Base aims to reserve at least 20% for members". "Batteries never drop below 20%, and it's rare that they ever even fall below 50%". Base says it recharges quickly after discharge "even ... during high-demand periods" at high cost. [P] [13]
- The minimum backup reserve is 5 h (1 battery) or 10 h (2 batteries) at low usage, which covers "97.5% of outages in the state of Texas". [P] help article [18]
- Grid support "draw[s] from available capacity above that reserve". [P] /utilities FAQ [16]
- In an outage the battery disconnects from the grid and powers the home only. It "will never discharge to the grid during an outage". [P] [13]
- 99% of outages are distribution-level. That is Base's argument for why discharging at price spikes does not collide with outage risk. [P] [13]
- The battery only engages backup if home load is under 20 kW; members can restart it from the app. [P] /how-it-works [19]

## 3. ERCOT participation

**ADER pilot rules (Phase 3.3, governing doc dated 2026-06-02)**
- Each ADER is one Load Resource per load zone, with the same LSE and DSP.
- As an Aggregate Load Resource (ALR) it is dispatched by SCED every 5 minutes and settled at the **Load Zone price**.
- An NCLR path added in Phase 3 lets aggregations that are not SCED-dispatchable offer AS through the AS Deployment Manager.
- Only ECRS and Non-Spin are eligible. RRS is "consider[ed] ... subject to a system-wide cap".
- Real-time telemetry is 2-second, and device-level validation data is required.
- Sources: [P] ERCOT ADER page [20]; governing doc 3.3 [21]; ERCOT Board Item 4.3, Jun 2025 [22]

**Pilot caps**

| Date | Registered capacity | Non-Spin | ECRS | Per-QSE cap | Source |
|---|---|---|---|---|---|
| Phase 2 | 80 MW | 40 MW | 40 MW | n/a | [P] [22] |
| Jun 2025 | 160 MW | 80 MW | 80 MW | n/a | [P] [22]; [S] [23] |
| 2026-03-02 (Market Notice M-A030226-01) | 500 MW | 100 MW (unchanged) | 100 MW (unchanged) | raised from 50% to **90%** | [P] [24] |

**[I]** The per-QSE increase is consistent with Base being over 50% of the program.

**ERCOT monthly ADER report (all participants, owners masked)** [P] [25]

| Month | Qualified energy | Non-Spin | ECRS | ADERs |
|---|---|---|---|---|
| 2025-05 | 15.5 MW | 8.6 MW | 8.8 MW | 3 |
| 2025-11 | 107.6 MW | 35.4 MW | 36.2 MW | 7 |
| 2026-04 | 235.7 MW | 46.9 MW | 77.8 MW | n/a |
| 2026-08 | **292.9 MW** | 64.5 MW | **97.3 MW** | 9 |

- Resources listed: AR, BAR, BOERNE, CIBOLO, MARION, MIDNT, OB, SANSM, WEBBS (all suffixed `_ALD1`).
- **[I, unverified]** MIDNT and SANSM appear in Jul 2025, OB in Dec 2025, and MARION in Mar 2026. All four share a pattern:
  - very high online bids, about $4,000 to $4,900;
  - "Dispatched" in most intervals;
  - AS offers of about **$0.01** at award, which makes them price-takers for AS.

  That pattern plausibly matches Base's partitions and timeline, but ERCOT masks QSE names.
- The report splits Dec 2025 into "PRE-RTC" and "RTC", so **Real-Time Co-optimization went live in Dec 2025**. After that, AS is co-optimized with energy in real time. That matters for how capacity gets committed.

**Base's own figures** [P] ADER Phase IV blog by Chase Dowling, Head of Markets [4]
- "Base constitutes 103 of 145 total MW registered ADER participants, or 71%", participating "24/7".
- Fleet nameplate discharge (MW) and ADER share by month:

  | Month | Fleet MW | ADER share |
  |---|---|---|
  | May 2025 | 28.9 | 0% |
  | Dec 2025 | 89.5 | 18% |
  | Mar 2026 | 136.3 | 32% |
  | May 2026 | 172.7 | 47% |
  | Jul 2026 | **205.5** | 39% |

- Partitions in Aug 2026: LZ North 22.9 MW, LZ South 7.2 MW, LZ Houston 50.5 MW, plus 30 MW Houston pending.
- About **60 days** from install to SCED participation. Each new tranche goes through registration, RIOO, ICCP, SCED testing, then AS testing, gated by network-model releases.
- Qualification services named: **Non-Spin and ECRS**.
- **Unverified.** The "103 of 145 MW" figure does not reconcile with the chart (80.6 MW ADER) or with ERCOT's 292.9 MW qualified in Aug 2026. It probably reflects a different date or metric (registered vs nameplate). Ask about it.

**Dispatch accuracy** [P] [4]
- Example: the lz-houston-ader partition on the evening of Jul 22, 2026. All 36 of 36 intervals were within CLREDP tolerance (the larger of 2 MW or 15% of max capability).
  - Mean absolute deviation was 1.59 MW; the largest was 5.45 MW, during a ramp.
  - Max discharge capability was 46.9 MW and max charge 46 MW.
  - Average error was 3.3% of commanded power.
- An interval is only scored if at least 80% of it has both telemetry and a setpoint. Telemetry held for more than 180 s is blanked as stale.
- Settlement uses premise AMI meters, which net house load against battery flow. Base is advocating for revenue-grade device meters (ANSI C12.1) to settle ADERs instead.

**Fleet and customer growth**
- More than 500 MWh installed; about 100 batteries/day, targeting 2x by end of 2026; $1B Series D at a $13B valuation (Aug 3, 2026). [S] TechCrunch [10]; Electrek [26]
- About 2 MW/day energized, "closing in on 1 GW installed per year". [P] [4]
- "30,000+ homeowners across Texas and Illinois" (homepage) [P] [2]. An older blog sidebar says "20,000+ homes" [P] [13].
- Series C ($1B, Oct 2025) context: [S] ess-news [27]

**Utility partnerships**

| Partner | What | Source |
|---|---|---|
| GVEC | A 2 MW pilot grew to **50 MW** across the full territory (announced 2026-04-13). The aggregation "qualified in ERCOT's ADER Pilot" and passed tests on the first attempt. Members pay $295 per unit. | [P] GVEC release [28]; [S] Utility Dive [29] |
| CoServ | **100 MW** over 2 years, about 5,000 homes, announced 2026-03-06. CoServ keeps "operational control". | [P] CoServ release [30]; [S] Canary Media [31] |
| El Paso Electric | Up to about 10 MW (about 1,000 batteries) before summer 2026. First install Mar 2026. Non-ERCOT (WECC). | [P] EPE release [32] |
| Austin Energy | Program launched Jul 2026. | [P] Business Wire release [33] |
| Farmers EC, Bandera EC | Listed as partners in the help center. | [P] [7] |
| Aggregate | "Over 200 MW" of utility capacity. /utilities: "3 metro areas, 6 utility partners", "96% fleet availability", "<5% forced outage rate", "up to 500 cycles a year". Utilities dispatch through an "Operators dashboard" or through EMS/SCADA integration. | [S] [10]; [P] [16] |

## 4. Tech and ops

**Hardware**
- Base Core: 39.2 kWh (78.4 kWh with two units), **20 kW** (per /utilities "20 kW / 39.2 kWh"), 50 ms auto-switch, -22 to 122 °F, 120/240 V, 200 A max service, UL 1973/9540/1741. [P] [12][16]
- Continuous vs peak inverter rating is not published. Unverified.
- Ground-mounted units: 25 or 50 kWh, **11 kW inverter**, LFP. [P] [7]
- Built at Base Factory 1 in Austin. [S] [26]

**Software stack (job postings)** [P]
- **BaseOS** "coordinates thousands of distributed batteries". Go and Python, Temporal ("workflow systems ... that manage deployments, device control"), Terraform, AWS, Docker/K8s. [14b]
- Firmware and embedded work in C, C++ or Rust, with OTA updates. Devices "respond to real-time grid conditions". [17b]
- System integration: gRPC/HTTP, CAN (ISO-TP/UDS), HIL CI. [17c]
- The data team ingests "firmware telemetry from thousands of deployed batteries, ERCOT market signals" through batch and streaming pipelines into time-series stores. Grafana is used throughout. [17d]
- Engineering blog: Temporal-based install-scheduling balancer. [P] [34]

**Control and telemetry path** [P] [4]
- Devices connect to Base's private cloud over **MQTT**, through a private backhaul.
- Base QSE connects to ERCOT over **ICCP** via a dedicated redundant WAN router, reaching the Bastrop and Taylor control centers.
- Base API plus a 24/7 on-call desk.
- Security claims: SOC2 cloud, encrypted edge, isolated edge devices.

**Outage and grid-commitment conflicts**
- Stated policy:
  - At the device level, backup always wins, because the battery islands and never exports during an outage. [P] [13]
  - At the fleet level, Base keeps a 20% floor plus the 5 h / 10 h minimum. [P] [13][18]
  - "Outage prediction technology" shapes charging. [P] [13]
- **[I]** Any home that islands, loses Wi-Fi, or exceeds 20 kW drops out of the aggregate. Those losses correlate by feeder and by storm. This is the gap your project targets.
- Base's public materials do not describe how it de-rates AS offers for this. Unverified.

**Events**
- There is no public Base account of fleet behavior during Winter Storm Fern (Jan 2026) or specific summer-2026 peaks. Unverified.
- Base's Jan 16, 2026 charge/discharge blocks moved flows at two 138 kV buses, measured in ERCOT state estimator data. [P] [4]

## 5. Job postings: what the Markets and software roles work on

- **Markets team charter** (shared text across the postings): "device communications with balancing authorities, telemetry analysis, algorithms for fleet aggregation and economic dispatch, through to financial portfolio management in wholesale energy markets". The team is physicists, economists, CS people and engineers, and "Success looks like building the airplane as it falls out of the sky." [P] [14][15][35]
- **Algorithms Engineer** (posted Apr 2026). [P] [14]
  - Describes "the fleet aggregation and distribution loop" as "a sequential decision making problem".
  - Builds dispatch for RT arbitrage and AS.
  - Integrates market algorithms with "grid-service control loops for voltage regulation and system peak shaving".
  - Controls for "concentrations of aggregated battery deployments at distribution system voltages".
  - Holds an on-call scheduling-engineer rotation and is a member of the trading desk.
  - Skills: MPC, RL, MDPs, signal processing.
- **Quantitative Developer Intern** (Sep 2026). [P] [15]
  - Designs trading algorithms for storage in ERCOT and validates physics and economics models in simulation.
  - Deploys to the "production trading stack".
  - Quantifies long-term financial risk under operating strategies and automates hedging, valuation and execution.
- **Market Operations Engineer** (May 2026). [P] [35]
  - ISO stakeholder work, asset deployment valuation, and support for the "real-time asset management team".
  - Python, Pandas, SQL, Grafana.
- **Data Engineer and Backend.** Telemetry plus ERCOT signals pipelines, and BaseOS services that "control real hardware in real homes". [P] [17d][14b]

## Questions for a Base engineer

1. **Sizing AS offers.** When you offer ECRS and Non-Spin for a partition, how do you get from telemetered available MW to the offer MW? Is it a fixed haircut or a fixed headroom percent, or is it computed from forecast availability, such as the chance a device islands, drops Wi-Fi, or is load-limited? How big is the haircut today?
2. **Correlated failure.** Do you model availability loss as correlated, for example by feeder, substation, or storm footprint, or as independent per device? Has a regional event such as a CenterPoint storm ever removed a large share of a partition while it was carrying AS?
3. **Backup vs obligation.** When members island during a grid event, the ADER's capability drops mid-interval. How does that flow into the telemetered HSL/NPC that SCED and the AS deployment use? Do you update it every 2 s, and how fast do you re-bid?
4. **The 20% floor and the 5/10-hour promise.** Is the reserve a fixed SOC, or is it dynamic with weather and outage-risk forecasts, such as pre-charging before storms? How does that reserve enter the AS energy-duration requirement? My understanding is that Non-Spin and ECRS need sustained MW; this is unverified.
5. **DAM vs RT.** Post-RTC (Dec 2025), do you carry AS mainly from real-time co-optimization awards or from day-ahead awards? ERCOT's monthly report shows ADER AS offers at about $0.01. Is that Base, and is it a deliberate price-taker strategy?
6. **Hourly commitment decision.** What decides how much to commit each hour? Is it an optimizer (MPC or stochastic program), a rules layer, or trader judgment on the RT desk? What are the state variables: SOC distribution, fleet availability, prices, weather?
7. **Telemetry loss.** Your blog blanks telemetry that is stale for more than 180 s. Operationally, what does the fleet controller assume about devices that go dark: last-known state, zero capability, or a statistical estimate? What share of the fleet is dark at a typical moment, and during storms?
8. **Shortfall history.** How often have you failed to meet an SCED base point or an AS deployment (CLREDP failures, AS non-performance)? What were the root causes, and what did each cost? Has any failure changed the headroom policy?
9. **Why only 39% is in ADER.** About 60% of nameplate is not enrolled in an ADER. Is that purely the 60-day registration and network-model lag, the pilot AS caps (ECRS about 97 of 100 MW), or utility-partner fleets? How is the non-ADER fleet monetized? Through the REP's load and 4CP-like peak reduction?
10. **The partner-utility conflict.** When CoServ or GVEC dispatch and ERCOT prices spike at the same time, who has priority? How do you stop double-committing the same kWh?
11. **Allocating a partition setpoint to devices.** How do you split an aggregate setpoint across thousands of homes? Is it SOC-balancing, rotating, or cost-based with degradation limits? How do you honor the "up to 500 cycles a year" figure?
12. **The "103 of 145 MW" figure.** How does that relate to ERCOT's 292.9 MW qualified and your 80.6 MW ADER partitions? That tells you which capacity number they actually plan around.

## Sources

[1] Base Texas REP Energy Terms of Service v20260601. https://bpc-web-static-files.s3.us-east-2.amazonaws.com/energy-docs/Energy+Terms+of+Service.pdf [P]
[2] Base Power homepage and footer. https://www.basepowercompany.com/ [P]
[3] PUCT Project 54311 filings (Base Texas QSE LLC, 2025-11-03). https://interchange.puc.texas.gov/Search/Filings?ControlNumber=54311 [P]
[4] Base blog, "What's happening in Texas can help solve the capacity crunch: ADER Phase IV" (Chase Dowling). https://www.basepowercompany.com/blog/aggregated-ders-and-the-capacity-crunch [P]
[5] LEI record, Base Texas QSE, LLC. https://lei.bloomberg.com/leis/view/254900UKUTGWSWGY2U04 [S]
[6] EnergyChoiceMatters, 2023-12-29. https://www.energychoicematters.com/stories/20231229aa.html [S]
[7] Base Help Center, Backup battery service. https://help.basepowercompany.com/en/categories/2347329-backup-battery-service [P]
[8] Base pricing by area. https://www.basepowercompany.com/pricing [P]
[9] Base Core page. https://www.basepowercompany.com/core [P]
[10] TechCrunch, 2026-08-03. https://techcrunch.com/2026/08/03/base-power-raises-another-1b-to-save-the-grid-using-backyard-batteries/ [S]
[11] Base Energy page. https://www.basepowercompany.com/energy [P]
[12] Base Core specs. https://www.basepowercompany.com/specs/core [P]
[13] Base blog, "Understanding how Base charges and discharges its batteries". https://www.basepowercompany.com/blog/how-base-charges-and-discharges-its-batteries [P]
[14] Algorithms Engineer posting. https://jobs.ashbyhq.com/base-power/a059e0be-15d9-4e98-94a0-8c99952a9f6a [P]
[14b] Software Engineer, Backend posting. https://jobs.ashbyhq.com/base-power/23bc4e94-d63c-4933-926b-edcb69138410 [P]
[15] Quantitative Developer Intern posting. https://jobs.ashbyhq.com/base-power/b6b2332e-1226-4575-b2c9-9e5258f2540e [P]
[16] Base utility partnerships page. https://www.basepowercompany.com/utilities [P]
[17] Server Architect posting https://jobs.ashbyhq.com/base-power/d5d28d03-cfaf-4d62-8543-65153023205c and Network Engineer, Distributed Compute https://jobs.ashbyhq.com/base-power/2a07a712-ae15-4e1b-87a7-375b75a1c7c7 [P]
[17b] Embedded Software Engineer posting. https://jobs.ashbyhq.com/base-power/85775865-f676-47b5-85e4-a7b4237f837c [P]
[17c] System Integration Engineer posting. https://jobs.ashbyhq.com/base-power/d5346efc-51b9-44f8-89f6-41f6178825c9 [P]
[17d] Data Engineer posting. https://jobs.ashbyhq.com/base-power/e0c632cd-2375-4e0f-8a9f-11dbb820e885 [P]
[18] Help: minimum hours of backup reserved. https://help.basepowercompany.com/en/articles/10283649 [P]
[19] Base How it works. https://www.basepowercompany.com/how-it-works [P]
[20] ERCOT ADER Pilot Project page. https://www.ercot.com/mktrules/pilots/ader [P]
[21] ERCOT ADER Governing Document Phase 3.3. https://www.ercot.com/files/docs/2026/03/02/ADER-Pilot-Project-Governing-Document-Phase-3.3.docx [P]
[22] ERCOT Board Item 4.3, ADER Phase 3 (Jun 2025). https://www.ercot.com/files/docs/2025/06/16/4.3-Aggregate-Distributed-Energy-Resource-ADER-Pilot-Project-Phase-3.pdf [P]
[23] EnergyChoiceMatters, 2025-06-20. http://www.energychoicematters.com/stories/20250620e.html [S]
[24] ERCOT Market Notice M-A030226-01. https://www.ercot.com/services/comm/mkt_notices/M-A030226-01 [P]
[25] ERCOT ADER Monthly Report 202506-202606 (xlsx) https://www.ercot.com/files/docs/2025/04/28/ADER_Monthly_Report_202506_202606.xlsx and Limits of Participation Tracking 06-01-2026 https://www.ercot.com/files/docs/2025/05/06/Limits-of-Participation-Tracking_06-01-2026.xlsx [P]
[26] Electrek, 2026-08-03. https://electrek.co/2026/08/03/base-power-raises-1b-to-roll-out-its-giant-new-home-battery/ [S]
[27] ESS News, 2025-10-09. https://www.ess-news.com/2025/10/09/base-power-hauls-in-1-billion-to-take-its-distributed-home-battery-model-beyond-texas/ [S]
[28] GVEC release, 2026-04-13. https://www.experienceguadalupevalley.com/news/p/item/67319/gvec-and-base-power-expand-partnership-to-full-service-territory-delivering-50-mw-of-residential-battery-capacity [P]
[29] Utility Dive, GVEC 2 MW VPP. https://www.utilitydive.com/news/base-power-gvec-texas-vpp-virtual-power-plant/752102/ [S]
[30] CoServ release. https://www.coserv.com/coserv-base-power-press-release/ [P]
[31] Canary Media. https://www.canarymedia.com/articles/batteries/base-power-to-launch-100-mw-home-battery-network-for-texas-utility [S]
[32] El Paso Electric release. https://www.epelectric.com/news/el-paso-electric-and-base-power-install-first-home-battery-in-el-paso-as-part-of-new-reliability-pilot-program [P]
[33] Business Wire, Austin Energy, 2026-07-15. https://www.businesswire.com/news/home/20260715702282/en/Base-Power-Brings-Affordable-Home-Backup-to-Austin-Energy-Customers [P]
[34] Base blog, self-scheduling (Temporal balancer). https://www.basepowercompany.com/blog/self-scheduling [P]
[35] Market Operations Engineer posting. https://jobs.ashbyhq.com/base-power/c66ad226-2196-48bc-aeb3-43d4170be345 [P]

**Not reachable or not checked.** I could not access the PUCT docket 58183 filing (HTTP 402). The Modo Energy podcast with Zach Dell (https://modoenergy.com/research/podcast-distributed-batteries-for-grid-resilience-with-zach-dell) had no transcript available. I did not check ERCOT's public QSE registration list directly.
