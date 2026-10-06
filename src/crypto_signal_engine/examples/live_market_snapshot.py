import asyncio
from datetime import timedelta

from crypto_signal_engine.collectors.binance import (
    BinanceFuturesTradeCollector,
    BinanceSpotOrderBookCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.collectors.bybit import (
    BybitFuturesTradeCollector,
    BybitSpotOrderBookCollector,
    BybitSpotTradeCollector,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    MarketSnapshotRepository,
    PredictionRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.domain.models import Exchange
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics
from crypto_signal_engine.market import MarketSnapshotAggregator
from crypto_signal_engine.predictions import BaselinePredictionEngine


async def consume_trades(collector, aggregator: MarketSnapshotAggregator) -> None:
    async for trade in collector.trades():
        await aggregator.update_trade(trade)


async def consume_order_book(
    exchange: Exchange,
    collector,
    aggregator: MarketSnapshotAggregator,
) -> None:
    async for snapshot in collector.snapshots():
        metrics = calculate_order_book_metrics(snapshot, depth_levels=20)
        await aggregator.update_order_book(
            exchange,
            metrics,
            event_time=snapshot.event_time,
        )


async def persist_snapshots(
    aggregator: MarketSnapshotAggregator,
    snapshot_repository: MarketSnapshotRepository,
    prediction_repository: PredictionRepository,
    prediction_engine: BaselinePredictionEngine,
    *,
    interval_seconds: float = 5.0,
    prediction_interval_seconds: int = 60,
) -> None:
    last_prediction_at = None
    horizons = (300, 900)

    while True:
        await asyncio.sleep(interval_seconds)
        snapshot = await aggregator.snapshot()

        await snapshot_repository.add(snapshot)

        evaluations = await prediction_repository.evaluate_due(snapshot.timestamp)
        for evaluation in evaluations:
            print(
                "EVALUATED "
                f"id={evaluation.prediction_id} "
                f"return={evaluation.return_pct:.4f}% "
                f"success={evaluation.success}"
            )

        should_generate = (
            last_prediction_at is None
            or snapshot.timestamp - last_prediction_at
            >= timedelta(seconds=prediction_interval_seconds)
        )

        if should_generate:
            for horizon_seconds in horizons:
                prediction = prediction_engine.generate(
                    snapshot,
                    horizon_seconds=horizon_seconds,
                )
                if prediction is not None:
                    await prediction_repository.add(prediction)
                    print(
                        "PREDICTION "
                        f"id={prediction.id} "
                        f"horizon={prediction.horizon_seconds}s "
                        f"direction={prediction.direction.value} "
                        f"raw_score={prediction.raw_score:.4f} "
                        f"entry={prediction.entry_price}"
                    )

            last_prediction_at = snapshot.timestamp

        print()
        print(f"{snapshot.symbol} | {snapshot.timestamp.isoformat()}")
        print(f"price={snapshot.price}")
        print(
            "cvd "
            f"binance_spot={snapshot.binance_spot_cvd} "
            f"binance_futures={snapshot.binance_futures_cvd} "
            f"bybit_spot={snapshot.bybit_spot_cvd} "
            f"bybit_futures={snapshot.bybit_futures_cvd}"
        )
        print(
            "flow "
            f"spot_total={snapshot.spot_cvd_total} "
            f"futures_total={snapshot.futures_cvd_total} "
            f"divergence={snapshot.spot_futures_divergence}"
        )
        print(
            "book "
            f"binance={snapshot.binance_book_imbalance} "
            f"bybit={snapshot.bybit_book_imbalance} "
            f"cross_exchange_div={snapshot.cross_exchange_book_divergence}"
        )
        print(
            "pressure "
            f"buy={snapshot.buy_pressure} "
            f"sell={snapshot.sell_pressure}"
        )
        print(
            "freshness_ms "
            f"binance_spot={snapshot.binance_spot_age_ms} "
            f"binance_futures={snapshot.binance_futures_age_ms} "
            f"bybit_spot={snapshot.bybit_spot_age_ms} "
            f"bybit_futures={snapshot.bybit_futures_age_ms} "
            f"binance_book={snapshot.binance_book_age_ms} "
            f"bybit_book={snapshot.bybit_book_age_ms}"
        )
        print(f"data_quality={snapshot.data_quality:.2f} persisted=yes")


async def main() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)

    await initialize_database(engine)

    session_factory = create_session_factory(engine)
    snapshot_repository = MarketSnapshotRepository(session_factory)
    prediction_repository = PredictionRepository(session_factory)
    prediction_engine = BaselinePredictionEngine()

    symbols = ["BTCUSDT"]
    aggregator = MarketSnapshotAggregator("BTCUSDT")

    try:
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(
                consume_trades(BinanceSpotTradeCollector(symbols), aggregator)
            )
            task_group.create_task(
                consume_trades(BinanceFuturesTradeCollector(symbols), aggregator)
            )
            task_group.create_task(
                consume_trades(BybitSpotTradeCollector(symbols), aggregator)
            )
            task_group.create_task(
                consume_trades(BybitFuturesTradeCollector(symbols), aggregator)
            )
            task_group.create_task(
                consume_order_book(
                    Exchange.BINANCE,
                    BinanceSpotOrderBookCollector(
                        symbols,
                        depth=20,
                        update_ms=100,
                    ),
                    aggregator,
                )
            )
            task_group.create_task(
                consume_order_book(
                    Exchange.BYBIT,
                    BybitSpotOrderBookCollector(
                        symbols,
                        depth=50,
                    ),
                    aggregator,
                )
            )
            task_group.create_task(
                persist_snapshots(
                    aggregator,
                    snapshot_repository,
                    prediction_repository,
                    prediction_engine,
                    interval_seconds=5.0,
                    prediction_interval_seconds=60,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
