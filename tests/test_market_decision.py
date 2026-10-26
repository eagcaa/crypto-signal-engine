from datetime import UTC, datetime
from types import SimpleNamespace

from crypto_signal_engine.watch.market_decision import (
    _atr, _ema, _rsi, market_decision_report,
)


def candle(price):
    return SimpleNamespace(
        close=price, high=price + 1, low=price - 1,
        close_time=datetime.now(UTC),
    )


def test_indicators_are_bounded():
    values = [100 + i * 0.1 for i in range(250)]
    assert _ema(values, 20) is not None
    assert 0 <= _rsi(values) <= 100
    assert _atr([candle(x) for x in values]) > 0


def test_insufficient_data_does_not_invent_signal():
    report = market_decision_report("BTCUSDT", [[candle(100)], [], [], []])
    assert "yeterli mum verisi yok" in report


def test_report_refuses_uncalibrated_probability():
    candles = [candle(100 + i * 0.1) for i in range(250)]
    report = market_decision_report(
        "BTCUSDT", [candles, candles, candles, candles]
    )
    assert "Yön kuralı henüz geçmiş veride doğrulanmadı." in report
    assert "Yukarı eğilim" not in report
    assert "Aşağı eğilim" not in report
    assert "düşük" in report


def test_mixed_ema_is_not_positive_evidence():
    prices = [100 + i * 0.1 for i in range(230)] + [123 - i * 0.3 for i in range(20)]
    candles = [candle(x) for x in prices]
    report = market_decision_report("BTCUSDT", [candles] * 4)
    assert "trend teyidi zayıf" in report
    assert "Yukarı eğilim" not in report
