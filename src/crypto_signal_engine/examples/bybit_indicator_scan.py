"""Print closed-candle indicator bias for BTC, ETH and SOL, not trade orders.

PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_indicator_scan
"""
import asyncio
import argparse

from crypto_signal_engine.collectors.bybit.candles import BybitLinearCandleClient
from crypto_signal_engine.collectors.bybit.perp_orderbook import BybitPerpOrderBookClient
from datetime import UTC, datetime
from crypto_signal_engine.signals.indicator_report import analyze
from crypto_signal_engine.signals.direction_forecast import forecast
from crypto_signal_engine.signals.combined_forecast import combine_forecast
from crypto_signal_engine.signals.forecast_journal import connect, record, settle, summary
from crypto_signal_engine.signals.price_scenario import scenarios


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--track', action='store_true', help='Persist predictions and settle earlier forecasts')
    parser.add_argument('--price-targets', action='store_true', help='Show uncalibrated 5m/15m price scenarios from closed 1m candles')
    parser.add_argument('--summary', action='store_true', help='Print saved forecast accuracy and exit')
    parser.add_argument('--db', default='data/bybit_forecasts.sqlite3')
    args = parser.parse_args()
    conn = connect(args.db) if args.track or args.summary else None
    if args.summary:
        for symbol, tf, horizon, confirmation, total, correct, wrong, flat in summary(conn):
            decisive = correct + wrong
            accuracy = f'{100 * correct / decisive:.1f}%' if decisive else 'N/A'
            print(f'{symbol} {tf} +{horizon}m {confirmation}: {correct}/{decisive} correct ({accuracy}), flat={flat}')
        conn.close()
        return
    client = BybitLinearCandleClient()
    book_client = BybitPerpOrderBookClient()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        if args.price_targets:
            try:
                one_minute = await client.fetch_closed(symbol, interval="1m", limit=120)
                for target in scenarios(one_minute):
                    print(f"{symbol} +{target.horizon_minutes}m: base={target.current:.4f} "
                          f"estimate={target.estimate:.4f} "
                          f"range=[{target.lower:.4f}, {target.upper:.4f}] "
                          f"change={target.expected_change_pct:+.3f}% "
                          f"asof={target.observed_at} (uncalibrated scenario)")
            except Exception as exc:
                print(f"{symbol} PRICE TARGET ERROR: {type(exc).__name__}: {exc}")
        if conn is not None:
            try:
                history = await client.fetch_closed(symbol, interval="1m", limit=999)
                print(f"{symbol} journal: settled {settle(conn, symbol, history)} earlier outcomes")
            except Exception as exc:
                print(f"{symbol} journal settlement error: {type(exc).__name__}: {exc}")
        book = None
        try:
            book = await book_client.fetch(symbol)
            age = (datetime.now(UTC) - book.timestamp).total_seconds()
            if age < -5 or age > 30:
                print(f"{symbol} ORDERBOOK: STALE ({age:.1f}s), ignored")
            else:
                print(f"{symbol} ORDERBOOK: {book.pressure} imbalance={book.imbalance:+.2%} "
                      f"bidUSDT={book.bid_notional:.0f} askUSDT={book.ask_notional:.0f} "
                      f"spread={book.spread_bps:.2f}bps age={age:.1f}s")
        except Exception as exc:
            print(f"{symbol} ORDERBOOK: unavailable {type(exc).__name__}: {exc}")
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
                combined = combine_forecast(prediction, book)
                print(f"  SONUC: {labels[combined.direction]} | orderbook={combined.confirmation}")
                print(f"  {combined.explanation}")
                if conn is not None:
                    candle = candles[-1]
                    age = (datetime.now(UTC) - candle.close_time).total_seconds()
                    if 0 <= age <= 90:
                        saved = record(conn, symbol=symbol, timeframe=interval,
                                       observed_at=candle.close_time, price=candle.close,
                                       direction=combined.direction,
                                       confirmation=combined.confirmation)
                        print(f"  JOURNAL: {saved} new forecasts saved")
                    else:
                        print(f"  JOURNAL: candle age {age:.0f}s; not recorded")
            except Exception as exc:
                print(f"{symbol} {interval}: DATA ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
