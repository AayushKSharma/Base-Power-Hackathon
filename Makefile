PY ?= .venv/bin/python

.PHONY: install test typecheck market-data market-report fixtures

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
