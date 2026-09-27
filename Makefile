PY ?= .venv/bin/python

.PHONY: install test typecheck market-data market-report fixtures base-actual base-actual-summary forecast-data forecast-report calibrate-quantiles replay chaos

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

# Live replay of the demo day. DAY=YYYY-MM-DD MINUTES= OUT= MARKET=
DAY ?= 2026-03-08
MINUTES ?= 4

replay:
	$(PY) -m harness replay --policy constant_haircut --param fraction=0.5 \
		--scenario scenarios/baseline.yaml --day $(DAY) --minutes $(MINUTES) \
		--seed 7 --out $(if $(OUT),$(OUT),replay) $(if $(MARKET),--market-dir $(MARKET))

# The same demo day with chaos/demo.yaml. Postgres is reused when it is already up.
chaos:
	@$(PY) -c "import psycopg; psycopg.connect('postgresql://harness:harness@127.0.0.1:54329/harness').close()" \
		|| docker compose up -d --wait
	$(PY) -m harness replay --policy constant_haircut --param fraction=0.5 \
		--scenario scenarios/baseline.yaml --day $(DAY) --minutes $(MINUTES) \
		--seed 7 --chaos chaos/demo.yaml \
		--database postgresql://harness:harness@127.0.0.1:54329/harness \
		--out $(if $(OUT),$(OUT),chaos-replay) $(if $(MARKET),--market-dir $(MARKET))
