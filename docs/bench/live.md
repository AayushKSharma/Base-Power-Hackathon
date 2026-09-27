# Live replay benchmark

An agent is a home. Ten agent-host processes share the fleet. The clock steps 2 simulated seconds. At 2 s the schedule kills 20% of the hosts. The operating day is 2026-03-08, seed 7. Reaction latency is simulated time until reported capability no longer includes those hosts (the 180 s stale rule). Recovery is simulated time until delivered MW is back on the commanded award. The tick rate is completed ticks divided by time inside the replay clock, after the fleet is built. Wall time includes that setup. A run that does not return within its wall-clock budget has no tick rate.

| Agents | Finished | Ticks | Wall (s) | Clock (s) | Ticks/s | Reaction p50 (s) | Reaction p99 (s) | Recovery p50 (s) | Recovery p99 (s) | Note |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 10000 | yes | 92 | 60.8 | 21.58 | 4.262 | 180.0 | 180.0 | 0.0 | 0.0 | killed 2 of 10 hosts |
| 50000 | yes | 92 | 161.8 | 111.69 | 0.824 | 180.0 | 180.0 | 0.0 | 0.0 | killed 2 of 10 hosts |
