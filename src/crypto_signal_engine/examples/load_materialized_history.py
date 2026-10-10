import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    ResearchFeatureRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.features.research import ResearchFeatureSnapshot


DECIMAL_FIELDS = {
    "price",
    "spot_cvd_1m",
    "spot_cvd_5m",
    "spot_cvd_15m",
    "futures_cvd_1m",
    "futures_cvd_5m",
    "futures_cvd_15m",
    "spot_cvd_ratio_1m",
    "spot_cvd_ratio_5m",
    "spot_cvd_ratio_15m",
    "futures_cvd_ratio_1m",
    "futures_cvd_ratio_5m",
    "futures_cvd_ratio_15m",
    "trend_score_5m",
    "trend_score_15m",
    "trend_score_1h",
    "trend_score_4h",
    "atr_pct_5m",
    "atr_pct_15m",
    "atr_pct_1h",
    "atr_pct_4h",
    "binance_oi_change_5m_pct",
    "binance_oi_change_15m_pct",
    "bybit_oi_change_5m_pct",
    "bybit_oi_change_15m_pct",
    "binance_funding_rate",
    "bybit_funding_rate",
    "binance_long_short_ratio",
    "bybit_long_short_ratio",
    "binance_top_trader_long_short_ratio",
    "binance_taker_buy_sell_ratio",
    "long_liquidations_5m_usd",
    "short_liquidations_5m_usd",
    "liquidation_imbalance_5m",
    "long_liquidations_15m_usd",
    "short_liquidations_15m_usd",
    "liquidation_imbalance_15m",
    "binance_book_imbalance",
    "bybit_book_imbalance",
    "market_data_quality",
}


def snapshot_from_json(payload: dict[str, object]) -> ResearchFeatureSnapshot:
    converted = dict(payload)
    converted["timestamp"] = datetime.fromisoformat(str(payload["timestamp"]))

    for name in DECIMAL_FIELDS:
        value = payload.get(name)
        converted[name] = None if value is None else Decimal(str(value))

    return ResearchFeatureSnapshot(**converted)


def iter_snapshots(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                yield snapshot_from_json(payload)
            except Exception as exc:
                raise ValueError(
                    f"Invalid historical feature JSONL at line {line_number}"
                ) from exc


async def load_file(
    *,
    path: Path,
    batch_size: int = 1000,
) -> tuple[int, int]:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    inserted = 0
    skipped = 0

    try:
        repository = ResearchFeatureRepository(create_session_factory(engine))
        batch: list[ResearchFeatureSnapshot] = []

        for snapshot in iter_snapshots(path):
            batch.append(snapshot)
            if len(batch) >= batch_size:
                added, ignored = await repository.add_many(
                    batch,
                    batch_size=batch_size,
                )
                inserted += added
                skipped += ignored
                batch.clear()

        if batch:
            added, ignored = await repository.add_many(
                batch,
                batch_size=batch_size,
            )
            inserted += added
            skipped += ignored

        print(
            "BACKFILL_LOADED "
            f"path={path} "
            f"inserted={inserted} "
            f"skipped_same_provenance={skipped}"
        )
        return inserted, skipped
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load materialized historical feature JSONL into PostgreSQL."
    )
    parser.add_argument("--path", required=True)
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        load_file(
            path=Path(args.path),
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
