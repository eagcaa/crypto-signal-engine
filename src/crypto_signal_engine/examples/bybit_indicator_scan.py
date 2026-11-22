"""Print closed-candle indicator bias for BTC, ETH and SOL, not trade orders.

PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_indicator_scan
"""
import asyncio

from crypto_signal_engine.collectors.bybit.candles import BybitLinearCandleClient
from crypto_signal_engine.signals.indicator_report import analyze
from crypto_signal_engine.signals.direction_forecast import forecast


async def main():
    client = BybitLinearCandleClient()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for interval in ("1m", "3m", "5m", "15m"):
            try:
                candles = await client.fetch_closed(symbol, interval=interval, limit=120)
                report = analyze(candles)
                print(f"{symbol} {interval} {report.close_time} {report.bias}")
                print(f"  price={report.price} MA20={report.ma20:.4f} MA50={report.ma50:.4f}")
                print(f"  MACD={report.macd:.5f} signal={report.macd_signal:.5f} hist={report.macd_histogram:.5f}")
                print(f"  KDJ K={report.k:.2f} D={report.d:.2f} J={report.j:.2f} VOL={report.volume_ratio:.2f}x")
                print(f"  previous-candle pivot={report.pivot:.4f} S1={report.support1:.4f} R1={report.resistance1:.4f}")
                print(f"  {report.explanation}")
                prediction = forecast(report)
                labels = {"RISE_EXPECTED": "YUKSELIS BEKLENTISI", "FALL_EXPECTED": "DUSUS BEKLENTISI", "UNCERTAIN": "YON BELIRSIZ"}
                print(f"  TAHMIN: {labels[prediction.direction]} | guc={prediction.strength}")
                print(f"  {prediction.explanation}")
            except Exception as exc:
                print(f"{symbol} {interval}: DATA ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
