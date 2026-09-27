# constant_haircut(fraction=0.9) on baseline

2026-08-17 to 2026-08-17, seed 7. The policy saw the P50 fleet.

## Where the numbers come from

Shortfall megawatt-hours are physical. The dollar shortfall is a secondary view (energy, plus $500/MW of compliance).

- RT MCPC: real data
- load-zone RT price: real data
- scarcity flag: assumption
- deployment probability: assumption
- fleet, SOC, and failures: assumption
- refill rate: assumption
- shortfall-cost preset: assumption
- compliance cost: assumption
- Set Point Deviation rate: assumption

## Scorecard

### P10

Backup-floor violations: 0.

- **ECRS:** revenue $841.61, revenue given up $0.00, net $-1,676.00, shortfall cost $2,517.61, shortfall 0.373 MW-h, 0 intervals left out of $.
  Calm 262 intervals (shortfall 0.093 MW-h), scarce 26 (shortfall 0.280 MW-h).
- **NONSPIN:** revenue $892.02, revenue given up $0.00, net $-1,377.15, shortfall cost $2,269.17, shortfall 0.189 MW-h, 0 intervals left out of $.
  Calm 266 intervals (shortfall 0.037 MW-h), scarce 22 (shortfall 0.153 MW-h).

### ECRS exceedance, P10

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### ECRS tolerance, P10

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 841.61 | 0.00 |
| 5 | 0.0% | 841.61 | 0.00 |
| 10 | 0.0% | 841.61 | 0.00 |
| 25 | 0.0% | 841.61 | 0.00 |

### NONSPIN exceedance, P10

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### NONSPIN tolerance, P10

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 892.02 | 0.00 |
| 5 | 0.0% | 892.02 | 0.00 |
| 10 | 0.0% | 892.02 | 0.00 |
| 25 | 0.0% | 892.02 | 0.00 |

### P25

Backup-floor violations: 0.

- **ECRS:** revenue $841.61, revenue given up $42.70, net $841.61, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 262 intervals (shortfall 0.000 MW-h), scarce 26 (shortfall 0.000 MW-h).
- **NONSPIN:** revenue $892.02, revenue given up $39.06, net $892.02, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 266 intervals (shortfall 0.000 MW-h), scarce 22 (shortfall 0.000 MW-h).

### ECRS exceedance, P25

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### ECRS tolerance, P25

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 841.61 | 42.70 |
| 5 | 0.0% | 841.61 | 42.70 |
| 10 | 0.0% | 841.61 | 42.70 |
| 25 | 0.0% | 841.61 | 42.70 |

### NONSPIN exceedance, P25

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### NONSPIN tolerance, P25

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 892.02 | 39.06 |
| 5 | 0.0% | 892.02 | 39.06 |
| 10 | 0.0% | 892.02 | 39.06 |
| 25 | 0.0% | 892.02 | 39.06 |

### P50

Backup-floor violations: 0.

- **ECRS:** revenue $841.61, revenue given up $93.51, net $841.61, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 262 intervals (shortfall 0.000 MW-h), scarce 26 (shortfall 0.000 MW-h).
- **NONSPIN:** revenue $892.02, revenue given up $99.11, net $892.02, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 266 intervals (shortfall 0.000 MW-h), scarce 22 (shortfall 0.000 MW-h).

### ECRS exceedance, P50

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### ECRS tolerance, P50

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 841.61 | 93.51 |
| 5 | 0.0% | 841.61 | 93.51 |
| 10 | 0.0% | 841.61 | 93.51 |
| 25 | 0.0% | 841.61 | 93.51 |

### NONSPIN exceedance, P50

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### NONSPIN tolerance, P50

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 892.02 | 99.11 |
| 5 | 0.0% | 892.02 | 99.11 |
| 10 | 0.0% | 892.02 | 99.11 |
| 25 | 0.0% | 892.02 | 99.11 |

### P75

Backup-floor violations: 0.

- **ECRS:** revenue $841.61, revenue given up $134.16, net $841.61, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 262 intervals (shortfall 0.000 MW-h), scarce 26 (shortfall 0.000 MW-h).
- **NONSPIN:** revenue $892.02, revenue given up $147.16, net $892.02, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 266 intervals (shortfall 0.000 MW-h), scarce 22 (shortfall 0.000 MW-h).

### ECRS exceedance, P75

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### ECRS tolerance, P75

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 841.61 | 134.16 |
| 5 | 0.0% | 841.61 | 134.16 |
| 10 | 0.0% | 841.61 | 134.16 |
| 25 | 0.0% | 841.61 | 134.16 |

### NONSPIN exceedance, P75

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### NONSPIN tolerance, P75

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 892.02 | 147.16 |
| 5 | 0.0% | 892.02 | 147.16 |
| 10 | 0.0% | 892.02 | 147.16 |
| 25 | 0.0% | 892.02 | 147.16 |

### P90

Backup-floor violations: 0.

- **ECRS:** revenue $841.61, revenue given up $154.48, net $841.61, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 262 intervals (shortfall 0.000 MW-h), scarce 26 (shortfall 0.000 MW-h).
- **NONSPIN:** revenue $892.02, revenue given up $171.17, net $892.02, shortfall cost $0.00, shortfall 0.000 MW-h, 0 intervals left out of $.
  Calm 266 intervals (shortfall 0.000 MW-h), scarce 22 (shortfall 0.000 MW-h).

### ECRS exceedance, P90

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### ECRS tolerance, P90

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 841.61 | 154.48 |
| 5 | 0.0% | 841.61 | 154.48 |
| 10 | 0.0% | 841.61 | 154.48 |
| 25 | 0.0% | 841.61 | 154.48 |

### NONSPIN exceedance, P90

```
MW (share of fleet)      P(hour >= x)
0 MW (0% of fleet)  100.0%  ####################
1 MW (5% of fleet)  0.0%
5 MW (25% of fleet)  0.0%
10 MW (50% of fleet)  0.0%
25 MW (125% of fleet)  0.0%
50 MW (250% of fleet)  0.0%
```

### NONSPIN tolerance, P90

| X MW | P(under-serve >= X in an hour) | Revenue $ | Revenue given up $ |
| ---: | ---: | ---: | ---: |
| 1 | 0.0% | 892.02 | 171.17 |
| 5 | 0.0% | 892.02 | 171.17 |
| 10 | 0.0% | 892.02 | 171.17 |
| 25 | 0.0% | 892.02 | 171.17 |
