import asyncio

from crypto_signal_engine.collectors.binance import BinanceSpotOrderBookCollector
from crypto_signal_engine.collectors.bybit import BybitSpotOrderBookCollector
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics


async def consume(name: str, collector) -> None:
    async for snapshot in collector.snapshots():
        metrics = calculate_order_book_metrics(snapshot, depth_levels=20)

        print(
            f"{name:<14} "
            f"{snapshot.symbol:<10} "
            f"bid={metrics.bid_volume} "
            f"ask={metrics.ask_volume} "
            f"imbalance={metrics.imbalance:.4f} "
            f"spread={metrics.spread}"
        )


async def main() -> None:
    symbols = ["BTCUSDT"]

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(
            consume(
                "binance-book",
                BinanceSpotOrderBookCollector(symbols, depth=20, update_ms=100),
            )
        )
        task_group.create_task(
            consume(
                "bybit-book",
                BybitSpotOrderBookCollector(symbols, depth=50),
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
