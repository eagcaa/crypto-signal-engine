.PHONY: help setup install test live live-log paper-live replay db-up db-down db-logs db-shell clean

PYTHON := .venv/bin/python
PYTHONPATH_SRC := PYTHONPATH=src
SYMBOL ?= BTCUSDT
HOURS ?= 6

help:
	@echo "Available commands:"
	@echo "  make setup     Create .venv and install project + dev dependencies"
	@echo "  make install   Install/update project in editable mode"
	@echo "  make test      Run test suite"
	@echo "  make live      Run live market snapshot collector"
	@echo "  make live-log  Run live collector and persist timestamped runtime log"
	@echo "  make paper-live Run live collector with local paper trading enabled"
	@echo "  make replay    Replay persisted history (SYMBOL=BTCUSDT HOURS=6)"
	@echo "  make db-up     Start TimescaleDB"
	@echo "  make db-down   Stop TimescaleDB"
	@echo "  make db-logs   Follow TimescaleDB logs"
	@echo "  make db-shell  Open psql shell"
	@echo "  make clean     Remove Python caches"

setup:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

install:
	@if [ ! -x "$(PYTHON)" ]; then 		echo ".venv not found. Run: make setup"; 		exit 1; 	fi
	$(PYTHON) -m pip install -e ".[dev]"

test:
	@if [ ! -x "$(PYTHON)" ]; then 		echo ".venv not found. Run: make setup"; 		exit 1; 	fi
	$(PYTHONPATH_SRC) $(PYTHON) -m pytest

live:
	@if [ ! -x "$(PYTHON)" ]; then 		echo ".venv not found. Run: make setup"; 		exit 1; 	fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.live_market_snapshot

live-log:
	@mkdir -p runtime-logs
	@echo "Writing live output to runtime-logs/"
	caffeinate -dimsu $(MAKE) live 2>&1 | tee -a runtime-logs/live_$(date +%Y%m%d_%H%M%S).log

replay:
	@if [ ! -x "$(PYTHON)" ]; then 		echo ".venv not found. Run: make setup"; 		exit 1; 	fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)"

db-up:
	docker compose up -d db

db-down:
	docker compose down

db-logs:
	docker compose logs -f db

db-shell:
	docker compose exec db psql -U crypto -d crypto_signal_engine

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
