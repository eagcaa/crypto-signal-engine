import asyncio
from collections import defaultdict

from crypto_signal_engine.collectors.binance import (
    BinanceFuturesTradeCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.collectors.bybit import (
    BybitFuturesTradeCollector,
    BybitSpotTradeCollector,
)
from crypto_signal_engine.domain.models import Exchange, MarketType, TradeTick
from crypto_signal_engine.features.cvd import CvdAccumulator


async def consume(
    collector_name: str,
    collector,
    accumulators: dict[tuple[Exchange, MarketType, str], CvdAccumulator],
) -> None:
    async for trade in collector.trades():
        key = (trade.exchange, trade.market_type, trade.symbol)
        cvd = accumulators[key].update(trade)

        print(
            f"{collector_name:<16} "
            f"{trade.symbol:<10} "
            f"{trade.side.value.upper():<4} "
            f"price={trade.price} "
            f"qty={trade.quantity} "
            f"cvd={cvd}"
        )


async def main() -> None:
    symbols = ["BTCUSDT"]

    accumulators: dict[
        tuple[Exchange, MarketType, str],
        CvdAccumulator,
    ] = defaultdict(CvdAccumulator)

    collectors = [
        (
            "binance-spot",
            BinanceSpotTradeCollector(symbols),
        ),
        (
            "binance-futures",
            BinanceFuturesTradeCollector(symbols),
        ),
        (
            "bybit-spot",
            BybitSpotTradeCollector(symbols),
        ),
        (
            "bybit-futures",
            BybitFuturesTradeCollector(symbols),
        ),
    ]

    async with asyncio.TaskGroup() as task_group:
        for name, collector in collectors:
            task_group.create_task(
                consume(
                    name,
                    collector,
                    accumulators,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
