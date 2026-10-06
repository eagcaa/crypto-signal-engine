import asyncio
from decimal import Decimal

from crypto_signal_engine.collectors.binance import BinanceSpotTradeCollector
from crypto_signal_engine.features.cvd import CvdAccumulator


async def main() -> None:
    collector = BinanceSpotTradeCollector(["BTCUSDT"])
    cvd = CvdAccumulator()

    async for trade in collector.trades():
        current_cvd: Decimal = cvd.update(trade)
        print(
            f"{trade.event_time.isoformat()} "
            f"{trade.symbol} "
            f"{trade.side.value.upper():4} "
            f"price={trade.price} "
            f"qty={trade.quantity} "
            f"cvd={current_cvd}"
        )


if __name__ == "__main__":
    asyncio.run(main())
