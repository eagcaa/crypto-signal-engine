from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.historical_price_forecast import predict_prices


def make_candles(count=500):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(count):
        price = Decimal(str(100 + i * 0.01 + (i % 11) * 0.03))
        candles.append(Candle(
            symbol="BTCUSDT", interval="1m",
            open_time=start + timedelta(minutes=i),
            close_time=start + timedelta(minutes=i + 1),
            open=price, high=price + 1, low=price - 1,
            close=price, volume=Decimal(1), quote_volume=price))
    return candles


def test_predict_prices_returns_point_and_validation():
    predictions = predict_prices(make_candles())
    assert [p.horizon_minutes for p in predictions] == [5, 15]
    assert all(p.estimate > 0 and p.validation_count >= 50 for p in predictions)
    assert all(p.model_mae_pct >= 0 and p.naive_mae_pct >= 0 for p in predictions)


def test_rejects_insufficient_history():
    with pytest.raises(ValueError, match="400"):
        predict_prices(make_candles(100))


def test_rejects_gapped_history():
    candles = make_candles()
    with pytest.raises(ValueError, match="Discontinuous"):
        predict_prices(candles[:200] + candles[201:])
