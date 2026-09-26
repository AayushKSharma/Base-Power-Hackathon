# Capacity-policy test harness

The harness replays a capacity policy on real post-RTC+B ERCOT market data and scores it. Project plan: [docs/handoff.md](docs/handoff.md).

```bash
make install        # .venv with the harness package and dev tools
make market-data    # build the ERCOT market dataset (see data/README.md)
make test           # offline tests from recorded ERCOT fixtures
make typecheck
```

## Components

- **Market dataset**: `harness.market`. ERCOT AS prices, load-zone prices, AS capability and demand curves on a 5-minute grid from Dec 5, 2025. It loads offline. See [data/README.md](data/README.md).
