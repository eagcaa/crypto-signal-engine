import argparse
import asyncio
import json
from datetime import date, datetime
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
    replace: bool = False,
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
                if replace:
                    added, ignored = await repository.replace_many(
                        batch,
                        batch_size=batch_size,
                    )
                else:
                    added, ignored = await repository.add_many(
                        batch,
                        batch_size=batch_size,
                    )
                inserted += added
                skipped += ignored
                batch.clear()

        if batch:
            if replace:
                added, ignored = await repository.replace_many(
                    batch,
                    batch_size=batch_size,
                )
            else:
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
            f"mode={'replace' if replace else 'insert'} "
            f"{'replaced' if replace else 'skipped_same_provenance'}={skipped}"
        )
        return inserted, skipped
    finally:
        await engine.dispose()


def default_materialized_path(
    *,
    symbol: str,
    days: int,
    end_day: date,
    input_root: Path,
    historical_version: str = "v1",
) -> Path:
    if days <= 0:
        raise ValueError("days must be positive")
    start_day = end_day.fromordinal(end_day.toordinal() - days + 1)
    prefix = (
        "features-v2"
        if historical_version == "v2"
        else "features"
    )
    return (
        input_root
        / symbol.upper()
        / "materialized"
        / f"{prefix}-{start_day.isoformat()}-{end_day.isoformat()}.jsonl"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load materialized historical feature JSONL into PostgreSQL."
    )
    parser.add_argument("--path", default="")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date", default="")
    parser.add_argument("--input-root", default="runtime-data/backfill")
    parser.add_argument(
        "--historical-version",
        choices=("v1", "v2"),
        default="v1",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace only rows with the same symbol/timestamp/provenance. "
            "Other provenances at the same timestamp are preserved."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.path:
        path = Path(args.path)
    else:
        if not args.end_date:
            raise ValueError("--end-date is required when --path is omitted")
        path = default_materialized_path(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            input_root=Path(args.input_root),
            historical_version=args.historical_version,
        )

    asyncio.run(
        load_file(
            path=path,
            batch_size=args.batch_size,
            replace=args.replace,
        )
    )


if __name__ == "__main__":
    main()
