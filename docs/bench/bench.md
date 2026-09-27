# Farm benchmark

One scenario-day is one completed job: a policy, a scenario, an operating day, and a seed.

## Throughput

| Workers | Scenario-days/min |
| ---: | ---: |
| 1 | 18.464 |
| 8 | 16.585 |
| 32 | 14.141 |

## Recovery

20% of the workers, rounded to the nearest count, are killed partway through the sweep. Time to finish is wall time from that kill until every job is done. Jobs re-run counts extra attempts.

| Workers | Killed | Time to finish (s) | Jobs re-run |
| ---: | ---: | ---: | ---: |
| 32 | 6 | 14.218 | 3 |
