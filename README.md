# Crypto Signal Engine

A real-time crypto market signal research engine focused on measurable, replayable and calibrated predictions.

The project collects live market microstructure data, transforms it into structured features, generates time-horizon-specific predictions, and evaluates every prediction after expiry.

## Initial scope

- Binance + Bybit live WebSocket collectors
- Spot and futures trade streams
- Order book tracking
- CVD and aggressive buy/sell flow
- Open interest and funding features
- Multi-timeframe market context
- Prediction storage and post-hoc evaluation
- Paper-only execution in the first phases

## Core principles

1. Confidence percentages must be calibrated from historical outcomes.
2. Every prediction is immutable and evaluated after its horizon expires.
3. Raw market data and derived features are stored separately.
4. Research, prediction and execution layers remain isolated.
5. Secrets never belong in the repository.

## Planned architecture

```text
src/crypto_signal_engine/
├── api/
├── collectors/
│   ├── binance/
│   └── bybit/
├── config/
├── domain/
├── evaluation/
├── features/
├── market/
├── prediction/
├── replay/
├── risk/
├── storage/
└── telegram/
```

## Technology stack

- Python 3.12+
- FastAPI
- asyncio
- PostgreSQL / TimescaleDB
- SQLAlchemy 2
- Alembic
- pytest
- Docker / Docker Compose

## Development

```bash
cp .env.example .env
docker compose up -d db

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
pytest
```

## Roadmap

1. Market data collectors
2. Order book and CVD features
3. Futures metrics
4. Feature snapshots
5. Prediction engine
6. Calibration
7. Automatic evaluation
8. Market replay
9. Telegram alerts
10. Paper trading

> This project starts as a research and paper-trading system. It should not be connected to live order execution until the strategy is validated out-of-sample.
