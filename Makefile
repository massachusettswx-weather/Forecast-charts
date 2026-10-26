PY      := venv/bin/python
PIP     := venv/bin/pip
RUN     ?= 20261026
CYC     ?= 1200
LEVEL   ?= 10
LAT     ?= 60

.PHONY: help venv sync sync-quick render site serve cache-clean clean test

help:
	@echo "make venv        - create the virtualenv and install deps"
	@echo "make sync        - fetch newest cycle, render 4 tabs, update the store"
	@echo "make sync-quick  - same but without ensemble member series (fast)"
	@echo "make render      - re-render the current cycle from the store"
	@echo "make site        - build the static site into site_out/"
	@echo "make serve       - run the FastAPI app on :8000"
	@echo "make cache-clean - drop the raw ERA5 cache (keep the slim copy)"

venv:
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

sync:
	$(PY) scripts/pipeline.py sync --level $(LEVEL) --lat $(LAT)

sync-quick:
	$(PY) scripts/pipeline.py sync --level $(LEVEL) --lat $(LAT) --quick

render:
	$(PY) scripts/pipeline.py sync --level $(LEVEL) --lat $(LAT) --force

site:
	$(PY) scripts/build_site.py --out site_out

serve:
	$(PY) -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000

cache-clean:
	rm -rf data/era5_cache data/idx
	rm -rf scripts/__pycache__ webapp/__pycache__

clean: cache-clean
	rm -rf site_out data/store/figures/*
