.PHONY: help setup install test live live-log paper-live candidate-paper-live paper-report shadow-report feature-coverage feature-diagnostics metrics-alignment readiness evaluation-report replay replay-exact replay-compare replay-holdout replay-walk-forward replay-historical replay-walk-forward-historical replay-walk-forward-historical-v2 backfill-history backfill-metrics validate-metrics-alignment backfill-materialize backfill-materialize-v2 backfill-load backfill-load-v2 calibrate db-up db-down db-logs db-shell clean

PYTHON := .venv/bin/python
PYTHONPATH_SRC := PYTHONPATH=src
SYMBOL ?= BTCUSDT
HOURS ?= 6
OFFSET ?= 12
WINDOWS ?= 6
DAYS ?= 7
START_DATE ?=
END_DATE ?=
END_AT ?=
PROVENANCE ?= binance_vision_historical_compatible_v1
PROVENANCE_V2 ?= binance_vision_historical_compatible_v2
METRICS_ARTIFACT ?=
METRICS_DAYS ?= 2
REPLACE ?= 0

help:
	@echo "Available commands:"
	@echo "  make setup        Create .venv and install project + dev dependencies"
	@echo "  make install      Install/update project in editable mode"
	@echo "  make test         Run test suite"
	@echo "  make live         Run live market snapshot collector"
	@echo "  make live-log     Run live collector and persist timestamped runtime log"
	@echo "  make paper-live   Run live collector with local paper trading enabled"
	@echo "  make candidate-paper-live Run only the frozen candidate paper tracker"
	@echo "  make paper-report Show persisted paper performance and validation"
	@echo "  make shadow-report Show 1h shadow gross/cost/net economics"
	@echo "  make feature-coverage Show persisted research feature history coverage"
	@echo "  make feature-diagnostics Raw feature vs 15m/1h/4h forward returns on Jul-Aug research only"
	@echo "  make metrics-alignment Rich OI/ratio observability diagnostic vs live Binance data"
	@echo "  make readiness    Check calibration and paper validation gates"
	@echo "  make evaluation-report Show evaluation source/version breakdown"
	@echo "  make replay       Replay persisted history (SYMBOL=BTCUSDT HOURS=6)"
	@echo "  make replay-exact Replay using historical Binance spot aggTrades"
	@echo "  make replay-compare Compare sampled versus exact first-touch outcomes"
	@echo "  make replay-holdout Validate an earlier non-overlapping replay window"
	@echo "  make replay-walk-forward Run exact sequential windows + candidate leaderboard"
	@echo "  make replay-historical Replay historical-compatible DB rows (END_AT required)"
	@echo "  make replay-walk-forward-historical Historical-compatible v1 walk forward"
	@echo "  make replay-walk-forward-historical-v2 Historical-compatible v2 walk forward"
	@echo "  make backfill-history Download/validate Binance Vision history (DAYS=7 END_DATE=YYYY-MM-DD)"
	@echo "  make backfill-metrics Download only Binance Vision USD-M metrics (START_DATE/END_DATE)"
	@echo "  make validate-metrics-alignment Compare archived metrics OI against persisted live Binance OI"
	@echo "  make backfill-materialize Convert downloaded v1 history to 60s features"
	@echo "  make backfill-materialize-v2 Convert history + validated metrics to v2 features"
	@echo "  make backfill-load Load materialized historical v1 features into PostgreSQL"
	@echo "  make backfill-load-v2 Load materialized historical v2 features into PostgreSQL"
	@echo "  make calibrate     Build exact replay calibration artifact"
	@echo "  make db-up        Start TimescaleDB"
	@echo "  make db-down      Stop TimescaleDB"
	@echo "  make db-logs      Follow TimescaleDB logs"
	@echo "  make db-shell     Open psql shell"
	@echo "  make clean        Remove Python caches"

setup:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

install:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHON) -m pip install -e ".[dev]"

test:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m pytest

live:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.live_market_snapshot

live-log:
	@mkdir -p runtime-logs
	@echo "Writing live output to runtime-logs/"
	caffeinate -dimsu $(MAKE) live 2>&1 | tee -a runtime-logs/live_$$(date +%Y%m%d_%H%M%S).log

paper-live:
	@mkdir -p runtime-logs
	@echo "Starting PAPER-ONLY live session; no exchange orders are sent."
	PAPER_TRADING_ENABLED=true caffeinate -dimsu $(MAKE) live 2>&1 | tee -a runtime-logs/paper_$(date +%Y%m%d_%H%M%S).log

candidate-paper-live:
	@mkdir -p runtime-logs
	@echo "Starting CANDIDATE PAPER-ONLY forward validation; no exchange orders are sent."
	PAPER_TRADING_ENABLED=false CANDIDATE_PAPER_ENABLED=true caffeinate -dimsu $(MAKE) live 2>&1 | tee -a runtime-logs/candidate_paper_$(date +%Y%m%d_%H%M%S).log

paper-report:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.paper_report

shadow-report:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.shadow_report --symbol "$(SYMBOL)" --hours "$(HOURS)"

feature-coverage:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.feature_coverage --symbol "$(SYMBOL)"

feature-diagnostics:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.feature_diagnostics --input "runtime-data/backfill/$(SYMBOL)/materialized/features-2026-07-01-2026-08-31.jsonl"

metrics-alignment:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.metrics_alignment --symbol "$(SYMBOL)" --days "$(METRICS_DAYS)" $(if $(END_DATE),--end-date "$(END_DATE)",)

readiness:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.readiness

evaluation-report:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.evaluation_report

replay:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)"

replay-exact:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)" --exact-binance-trades

replay-compare:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)" --compare-price-sources

replay-holdout:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)" --end-offset-hours "$(OFFSET)" --compare-price-sources

replay-walk-forward:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_walk_forward --symbol "$(SYMBOL)" --window-hours "$(HOURS)" --windows "$(WINDOWS)" --end-offset-hours "$(OFFSET)"

replay-historical:
	@if [ -z "$(END_AT)" ]; then echo "END_AT is required, e.g. END_AT=2026-09-01T00:00:00+00:00"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)" --end-at "$(END_AT)" --dataset-provenance "$(PROVENANCE)" --historical-compatible --exact-binance-trades

replay-walk-forward-historical:
	@if [ -z "$(END_AT)" ]; then echo "END_AT is required, e.g. END_AT=2026-09-01T00:00:00+00:00"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_walk_forward --symbol "$(SYMBOL)" --window-hours "$(HOURS)" --windows "$(WINDOWS)" --end-at "$(END_AT)" --dataset-provenance "$(PROVENANCE)" --historical-compatible

replay-walk-forward-historical-v2:
	@if [ -z "$(END_AT)" ]; then echo "END_AT is required, e.g. END_AT=2026-09-01T00:00:00+00:00"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_walk_forward --symbol "$(SYMBOL)" --window-hours "$(HOURS)" --windows "$(WINDOWS)" --end-at "$(END_AT)" --dataset-provenance "$(PROVENANCE_V2)" --historical-v2

backfill-history:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.backfill_history --symbol "$(SYMBOL)" --days "$(DAYS)" $(if $(END_DATE),--end-date "$(END_DATE)",)

backfill-metrics:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(START_DATE)" ] || [ -z "$(END_DATE)" ]; then echo "START_DATE and END_DATE are required, e.g. START_DATE=2026-10-08 END_DATE=2026-10-10"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.backfill_metrics --symbol "$(SYMBOL)" --start-date "$(START_DATE)" --end-date "$(END_DATE)"

validate-metrics-alignment:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(START_DATE)" ] || [ -z "$(END_DATE)" ]; then echo "START_DATE and END_DATE are required, e.g. START_DATE=2026-10-08 END_DATE=2026-10-10"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.validate_metrics_alignment --symbol "$(SYMBOL)" --start-date "$(START_DATE)" --end-date "$(END_DATE)"

backfill-materialize:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(END_DATE)" ]; then echo "END_DATE is required, e.g. END_DATE=2026-09-30"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.materialize_history --symbol "$(SYMBOL)" --days "$(DAYS)" --end-date "$(END_DATE)"

backfill-materialize-v2:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(END_DATE)" ]; then echo "END_DATE is required, e.g. END_DATE=2026-08-31"; exit 1; fi
	@if [ -z "$(METRICS_ARTIFACT)" ]; then echo "METRICS_ARTIFACT is required and must point to a PASS alignment artifact"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.materialize_history_v2 --symbol "$(SYMBOL)" --days "$(DAYS)" --end-date "$(END_DATE)" --metrics-alignment-artifact "$(METRICS_ARTIFACT)"

backfill-load-v2:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(END_DATE)" ]; then echo "END_DATE is required, e.g. END_DATE=2026-08-31"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.load_materialized_history --symbol "$(SYMBOL)" --days "$(DAYS)" --end-date "$(END_DATE)" --historical-version v2 $(if $(filter 1 true yes,$(REPLACE)),--replace,)

backfill-load:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	@if [ -z "$(END_DATE)" ]; then echo "END_DATE is required, e.g. END_DATE=2026-09-30"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.load_materialized_history --symbol "$(SYMBOL)" --days "$(DAYS)" --end-date "$(END_DATE)" $(if $(filter 1 true yes,$(REPLACE)),--replace,)

calibrate:
	@if [ ! -x "$(PYTHON)" ]; then echo ".venv not found. Run: make setup"; exit 1; fi
	$(PYTHONPATH_SRC) $(PYTHON) -m crypto_signal_engine.examples.replay_history --symbol "$(SYMBOL)" --hours "$(HOURS)" --exact-binance-trades --write-calibration "runtime-data/calibration.json"

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
