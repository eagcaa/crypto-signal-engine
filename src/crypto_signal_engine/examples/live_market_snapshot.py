import asyncio
from datetime import timedelta

from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceFuturesTradeCollector,
    BinanceLiquidationCollector,
    BinanceSpotOrderBookCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.collectors.bybit import (
    BybitDerivativesClient,
    BybitFuturesTradeCollector,
    BybitLiquidationCollector,
    BybitSpotOrderBookCollector,
    BybitSpotTradeCollector,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    CoinGlassSnapshotRepository,
    DerivativesRepository,
    MarketSnapshotRepository,
    PredictionRepository,
    ResearchFeatureRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.domain.models import Exchange
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics
from crypto_signal_engine.features.research import ResearchFeatureAggregator
from crypto_signal_engine.integrations import CoinGlassClient
from crypto_signal_engine.integrations.coinglass import CoinGlassApiError
from crypto_signal_engine.market import MarketSnapshotAggregator
from crypto_signal_engine.predictions import BaselinePredictionEngine


async def consume_trades(
    collector,
    aggregator: MarketSnapshotAggregator,
    research_aggregator: ResearchFeatureAggregator,
) -> None:
    async for trade in collector.trades():
        await aggregator.update_trade(trade)
        await research_aggregator.update_trade(trade)


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






async def poll_derivatives(
    client,
    repository: DerivativesRepository,
    research_aggregator: ResearchFeatureAggregator,
    *,
    symbol: str,
    interval_seconds: float = 30.0,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_snapshot(symbol)
            await repository.add_snapshot(snapshot)
            await research_aggregator.update_derivatives(snapshot)

            print(
                "DERIVATIVES "
                f"exchange={snapshot.exchange.value} "
                f"oi={snapshot.open_interest} "
                f"oi_usd={snapshot.open_interest_value_usd} "
                f"oi_5m={snapshot.oi_change_5m_pct}% "
                f"oi_15m={snapshot.oi_change_15m_pct}% "
                f"funding={snapshot.funding_rate} "
                f"long_short={snapshot.long_short_ratio} "
                f"top_trader={snapshot.top_trader_long_short_ratio} "
                f"taker_buy_sell={snapshot.taker_buy_sell_ratio}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"DERIVATIVES unavailable: {type(client).__name__}: {exc}")

        await asyncio.sleep(interval_seconds)


async def consume_liquidations(
    collector,
    repository: DerivativesRepository,
    research_aggregator: ResearchFeatureAggregator,
) -> None:
    async for event in collector.events():
        await repository.add_liquidation(event)
        await research_aggregator.update_liquidation(event)
        print(
            "LIQUIDATION "
            f"exchange={event.exchange.value} "
            f"symbol={event.symbol} "
            f"side={event.position_side.value} "
            f"notional_usd={event.notional_usd:.2f} "
            f"price={event.price}"
        )


async def poll_coinglass(
    client: CoinGlassClient,
    repository: CoinGlassSnapshotRepository,
    *,
    symbol: str,
    interval_seconds: float = 20.0,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_market_snapshot(symbol=symbol)
            await repository.add(snapshot)

            print(
                "COINGLASS "
                f"oi_usd={snapshot.open_interest_usd} "
                f"oi_5m={snapshot.oi_change_5m_pct}% "
                f"oi_15m={snapshot.oi_change_15m_pct}% "
                f"funding_binance={snapshot.funding_rate_binance} "
                f"funding_bybit={snapshot.funding_rate_bybit} "
                f"taker_buy={snapshot.taker_buy_ratio}% "
                f"taker_sell={snapshot.taker_sell_ratio}%"
            )
        except (CoinGlassApiError, TimeoutError) as exc:
            print(f"COINGLASS unavailable: {exc}")
        except Exception as exc:
            print(f"COINGLASS unexpected error: {exc}")

        await asyncio.sleep(interval_seconds)


async def persist_snapshots(
    aggregator: MarketSnapshotAggregator,
    snapshot_repository: MarketSnapshotRepository,
    prediction_repository: PredictionRepository,
    research_repository: ResearchFeatureRepository,
    research_aggregator: ResearchFeatureAggregator,
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

        research_snapshot = await research_aggregator.snapshot(snapshot)
        await research_repository.add(research_snapshot)

        print(
            "FEATURES "
            f"spot_cvd_1m={research_snapshot.spot_cvd_1m} "
            f"spot_cvd_5m={research_snapshot.spot_cvd_5m} "
            f"futures_cvd_1m={research_snapshot.futures_cvd_1m} "
            f"futures_cvd_5m={research_snapshot.futures_cvd_5m} "
            f"trade_sources={research_snapshot.spot_trade_sources}/"
            f"{research_snapshot.futures_trade_sources} "
            f"liq_5m={research_snapshot.liquidation_imbalance_5m} "
            f"binance_oi_5m={research_snapshot.binance_oi_change_5m_pct} "
            f"bybit_oi_5m={research_snapshot.bybit_oi_change_5m_pct}"
        )

        evaluations = await prediction_repository.evaluate_due(snapshot.timestamp)
        for evaluation in evaluations:
            if evaluation.return_pct is None:
                print(
                    "EVALUATED "
                    f"id={evaluation.prediction_id} "
                    f"status={evaluation.status.value}"
                )
            else:
                print(
                    "EVALUATED "
                    f"id={evaluation.prediction_id} "
                    f"status={evaluation.status.value} "
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
    coinglass_repository = CoinGlassSnapshotRepository(session_factory)
    derivatives_repository = DerivativesRepository(session_factory)
    research_repository = ResearchFeatureRepository(session_factory)
    prediction_engine = BaselinePredictionEngine()

    symbols = ["BTCUSDT"]
    aggregator = MarketSnapshotAggregator("BTCUSDT")
    research_aggregator = ResearchFeatureAggregator("BTCUSDT")

    try:
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(
                consume_trades(
                    BinanceSpotTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                )
            )
            task_group.create_task(
                consume_trades(
                    BinanceFuturesTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                )
            )
            task_group.create_task(
                consume_trades(
                    BybitSpotTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                )
            )
            task_group.create_task(
                consume_trades(
                    BybitFuturesTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                )
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
                    research_repository,
                    research_aggregator,
                    prediction_engine,
                    interval_seconds=5.0,
                    prediction_interval_seconds=60,
                )
            )

            task_group.create_task(
                poll_derivatives(
                    BinanceDerivativesClient(),
                    derivatives_repository,
                    research_aggregator,
                    symbol="BTCUSDT",
                    interval_seconds=30.0,
                )
            )
            task_group.create_task(
                poll_derivatives(
                    BybitDerivativesClient(),
                    derivatives_repository,
                    research_aggregator,
                    symbol="BTCUSDT",
                    interval_seconds=30.0,
                )
            )
            task_group.create_task(
                consume_liquidations(
                    BinanceLiquidationCollector(symbols),
                    derivatives_repository,
                    research_aggregator,
                )
            )
            task_group.create_task(
                consume_liquidations(
                    BybitLiquidationCollector(symbols),
                    derivatives_repository,
                    research_aggregator,
                )
            )

            if settings.coinglass_enabled and settings.coinglass_api_key:
                task_group.create_task(
                    poll_coinglass(
                        CoinGlassClient(settings.coinglass_api_key),
                        coinglass_repository,
                        symbol="BTCUSDT",
                        interval_seconds=20.0,
                    )
                )
            elif settings.coinglass_enabled:
                print(
                    "COINGLASS enabled but COINGLASS_API_KEY is empty; "
                    "integration disabled."
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
