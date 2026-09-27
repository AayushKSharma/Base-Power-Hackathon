PY ?= .venv/bin/python

.PHONY: install test typecheck market-data market-report fixtures base-actual base-actual-summary forecast-data forecast-report calibrate-quantiles backtest bench replay chaos

install:
	python3 -m venv .venv
	$(PY) -m pip install -e '.[dev]'

test:
	$(PY) -m pytest -q

typecheck:
	$(PY) -m mypy

# Build or extend the ERCOT market dataset (Dec 5, 2025 to yesterday). Only
# missing days are fetched. Pass START=YYYY-MM-DD / END=YYYY-MM-DD to narrow it.
market-data:
	$(PY) -m harness.market build $(if $(START),--start $(START)) $(if $(END),--end $(END))

market-report:
	$(PY) -m harness.market report

# Re-record tests/fixtures/raw from ERCOT (needs the network).
fixtures:
	$(PY) scripts/record_fixtures.py

# Ingest Base's ALR rows from the 60-Day SCED Disclosure (NP3-965-ER), Dec 5, 2025 to
# the latest published day (60 days ago). About 55 MB of download per day; only
# missing days are fetched. Pass START=YYYY-MM-DD / END=YYYY-MM-DD to narrow it.
base-actual:
	$(PY) -m harness.base_actual build $(if $(START),--start $(START)) $(if $(END),--end $(END))

base-actual-summary:
	$(PY) -m harness.base_actual summary --events

# Build or extend the point-in-time forecast-input store. Without an ERCOT API
# key this covers what MIS still keeps (about a week). Pass START= / END= to narrow it.
forecast-data:
	$(PY) -m harness.forecast build $(if $(START),--start $(START)) $(if $(END),--end $(END))

forecast-report:
	$(PY) -m harness.forecast report

# Quantile mocks from ingested Base-actual days (offline). Pass START=YYYY-MM-DD
# and END=YYYY-MM-DD. Optional STORE= (default data/market) and OUT=
# (default data/calibration/base-actual).
calibrate-quantiles:
	@test -n "$(START)" && test -n "$(END)" || { echo "pass START=YYYY-MM-DD END=YYYY-MM-DD"; exit 1; }
	$(PY) -m harness.calibration --store-dir $(if $(STORE),$(STORE),data/market) \
		--start $(START) --end $(END) --out $(if $(OUT),$(OUT),data/calibration/base-actual)

# Headline comparison. Same flags as docs/insights/compare-findings.md.
# Pass MARKET_DIR= to score against a dataset other than data/market.
backtest:
	$(PY) -m harness compare \
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
		--seed 7 $(if $(MARKET_DIR),--market-dir $(MARKET_DIR)) \
		--out docs/insights/compare

# Run-farm throughput and recovery. Needs the Postgres in compose.yaml.
# Writes docs/bench/bench.md. The recorded table is the one already in that file.
bench:
	$(PY) -m harness bench \
		--sweep docs/bench/sweep.yaml $(if $(MARKET_DIR),--market-dir $(MARKET_DIR)) \
		--out docs/bench

# Live replay of the demo day: 2026-08-17, the day the headline comparison charts.
# DAY= MINUTES= OUT= override the defaults.
DAY ?= 2026-08-17
MINUTES ?= 5

replay:
	$(PY) -m harness replay --policy constant_haircut \
		--scenario baseline --day $(DAY) --minutes $(MINUTES) \
		--seed 7 --out $(if $(OUT),$(OUT),data/replay) $(if $(MARKET_DIR),--market-dir $(MARKET_DIR))

# The same demo day with chaos/demo.yaml. Postgres is reused when it is already up.
chaos:
	@$(PY) -c "import psycopg; psycopg.connect('postgresql://harness:harness@127.0.0.1:54329/harness').close()" \
		|| docker compose up -d --wait
	$(PY) -m harness replay --policy constant_haircut \
		--scenario baseline --day $(DAY) --minutes $(MINUTES) \
		--seed 7 --chaos chaos/demo.yaml \
		--database postgresql://harness:harness@127.0.0.1:54329/harness \
		--out $(if $(OUT),$(OUT),data/replay) $(if $(MARKET_DIR),--market-dir $(MARKET_DIR))
