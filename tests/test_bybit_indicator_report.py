from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.indicator_report import analyze, kdj


def sample(count=120, direction=1, interval="5m"):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    result = []
    minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}[interval]
    for i in range(count):
        p = Decimal(100) + Decimal(direction * i) / 10
        opened = start + timedelta(minutes=i * minutes)
        result.append(Candle(symbol="BTCUSDT", interval=interval,
                             open_time=opened, close_time=opened + timedelta(minutes=minutes),
                             open=p, high=p + 1, low=p - 1, close=p,
                             volume=Decimal(100)))
    return result


def test_uptrend_bias():
    report = analyze(sample(direction=1))
    assert report.bias == "NEUTRAL"  # Linear trend: MACD and signal converge; no fresh momentum confirmation
    assert report.ma20 > report.ma50
    assert report.macd > 0
    assert report.volume_ratio == 1


def test_downtrend_bias():
    report = analyze(sample(direction=-1))
    assert report.bias == "NEUTRAL"  # Linear trend: MACD and signal converge; no fresh momentum confirmation
    assert report.ma20 < report.ma50
    assert report.macd < 0


def test_rejects_short_history():
    with pytest.raises(ValueError):
        analyze(sample(count=20))


def test_rejects_missing_candle():
    rows = sample()
    rows.pop(30)
    with pytest.raises(ValueError, match="Discontinuous"):
        analyze(rows)


def test_kdj_constant_prices():
    rows = sample(direction=0)
    k, d, j = kdj(rows)
    assert all(value.is_finite() for value in (k, d, j))
