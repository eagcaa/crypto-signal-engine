import asyncio

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
from crypto_signal_engine.domain.models import Exchange
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics
from crypto_signal_engine.market import MarketSnapshotAggregator


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


async def print_snapshots(
    aggregator: MarketSnapshotAggregator,
    *,
    interval_seconds: float = 5.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        snapshot = await aggregator.snapshot()

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
        print(f"data_quality={snapshot.data_quality:.2f}")


async def main() -> None:
    symbols = ["BTCUSDT"]
    aggregator = MarketSnapshotAggregator("BTCUSDT")

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
            print_snapshots(
                aggregator,
                interval_seconds=5.0,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
