"""Run Bybit V1 research signals against live public *closed* candles.

Usage: python -m crypto_signal_engine.examples.bybit_signal_scan
No exchange credentials or order permissions are used.
"""
import asyncio
from decimal import Decimal

from crypto_signal_engine.collectors.bybit.candles import BybitLinearCandleClient
from crypto_signal_engine.signals.bybit_v1 import evaluate


async def scan() -> None:
    client = BybitLinearCandleClient()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        try:
            candles = await asyncio.gather(*(
                client.fetch_closed(symbol, interval=interval, limit=120)
                for interval in ("1m", "5m", "15m")
            ))
            # Closed timeframes must align to prevent stale cross-timeframe confirmation.
            anchor = candles[2][-1].close_time
            aligned = [[c for c in series if c.close_time <= anchor] for series in candles]
            decisions = evaluate(symbol, *aligned, equity=Decimal("1000"))
            for signal in decisions:
                print(
                    f"{signal.symbol} {signal.strategy}: {signal.side} "
                    f"entry={signal.entry} SL={signal.stop} TP={signal.take_profit} "
                    f"leverage={str(signal.leverage) + 'x' if signal.leverage is not None else '-'} notional={signal.notional} "
                    f"reason={signal.reason}"
                )
        except Exception as exc:
            print(f"{symbol}: WAIT - data unavailable: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(scan())
