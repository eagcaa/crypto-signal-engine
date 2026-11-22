from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.research.price_backtest import evaluate


def test_chronological_backtest_produces_separate_validation_and_test():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(2100):
        price = Decimal(100) + Decimal(i % 27) / 100 + Decimal(i) / 10000
        candles.append(Candle(
            symbol="BTCUSDT", interval="1m",
            open_time=start + timedelta(minutes=i),
            close_time=start + timedelta(minutes=i + 1),
            open=price, high=price + 1, low=price - 1,
            close=price, volume=Decimal(1), quote_volume=price))
    results = evaluate(candles)
    assert [r["horizon"] for r in results] == [5, 15]
    assert all(r["test_n"] > 0 for r in results)
    assert all(r["status"] in ("PASS", "FAIL_NO_EDGE") for r in results)
