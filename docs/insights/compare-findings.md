# Insight check: policies on a post-RTC+B week

One week of real ERCOT prices, 11–17 August 2026, on the placeholder 1,000-home quantile fleet. The policies are the fixed haircut, the independent newsvendor, the correlated newsvendor, and the reliability target. The frontier also draws the reliability target at ε = 0.01, 0.05, 0.1, and 0.2. Scenarios are `baseline`, `caps_lifted` (pilot cap 100 MW → 500 MW), and `nonspin_2h` (Non-Spin held for 2 h instead of 4 h). Each preset has its own failure and deployment draws, because the scenario name is part of the random key. Policies inside one scenario share those draws.

Prices are real. The fleet, the failure model, and deployment chances are still the baseline assumptions.

Reproduce it from this worktree:

```
PYTHONPATH=src /Users/yush/Documents/Career/Programming/Base-Power-Hackathon/.venv/bin/python -m harness compare \
  --policy constant_haircut \
  --policy independent_newsvendor \
  --policy correlated_newsvendor \
  --policy reliability_target \
  --scenario baseline \
  --scenario caps_lifted \
  --scenario nonspin_2h \
  --start 2026-08-11 \
  --end 2026-08-17 \
  --day 2026-08-17 \
  --seed 7 \
  --market-dir /Users/yush/Documents/Career/Programming/Base-Power-Hackathon/data/market \
  --out docs/insights/compare
```

Charts, the tolerance tables, and the scorecard are in `docs/insights/compare/`. The capability chart is 17 August, the day in this week with the highest settlement prices (ECRS $22.90/MW-h, Non-Spin $91.53/MW-h).

## Does the harness separate policies?

Yes, on dollars. No, on shortfall for the fleet the policy actually saw.

On the P50 fleet every policy reports, shortfall is 0.000 MW-h and the overstatement rate is 0% in all three scenarios. Net $ still spreads. On baseline the independent newsvendor clears $3,804.63 and leaves $251.42 of revenue unclaimed. The reliability target at ε = 0.01 clears $3,111.65 and leaves $971.17. The fixed haircut sits between them, at $3,652.45 net and $405.83 given up.

## What does the frontier look like?

A flat line. Every point, including the reliability-target curve, sits at zero shortfall MW-h. Moving ε from 0.01 to 0.2 only slides the point right, toward more revenue. A tighter ε buys nothing in reliability on this fleet, because nothing was short. It only gives up revenue.

## Where do shortfalls concentrate?

Not in scarce hours, and not on the P50 fleet. Calm and scarce shortfall are both 0.000 MW-h on the fleet each policy saw.

The tolerance tables put the only under-serving on the P10 fleet, and only at 1 MW, never at 5 MW. On baseline P10 ECRS, P(an hour under-serves by at least 1 MW) is 0% for the haircut and for ε = 0.01, 3.6% for the correlated newsvendor, and about 16% for the independent newsvendor and for ε of 0.05 and above. Non-Spin stays at 0% on baseline P10 except a little revenue given up. With Non-Spin at 2 h, the looser policies also show about a 20% chance of a 1 MW Non-Spin hour on P10. The shortfalls are small and they sit on the thin quantile, not on the typical fleet.

## Do rankings flip?

They hold when the cap lifts, and they flip when Non-Spin drops to 2 h.

Best net $ first, on baseline and on `caps_lifted`: independent newsvendor, reliability target ε = 0.2, constant haircut, correlated newsvendor, then ε = 0.1, 0.05, 0.01. Lifting the cap from 100 MW to 500 MW does not reorder anyone. The fleet is far under the 100 MW cap, so the cap was not binding. Dollar levels still move a little between those two presets because their draws differ.

On `nonspin_2h` the correlated newsvendor passes both the haircut and ε = 0.2. The independent newsvendor stays first, and ε = 0.01 stays last. Non-Spin revenue roughly doubles for the haircut (about $2,038 to $3,982 on P10), which is the 2-hour duration letting the same energy support more MW. That is the ranking change with a real effect behind it. The close race on baseline between the haircut ($3,652) and the correlated newsvendor ($3,626) is too narrow to treat as a paired result, because the presets do not share draws.
