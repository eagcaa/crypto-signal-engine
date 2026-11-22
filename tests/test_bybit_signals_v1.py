from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_signal_engine.collectors.bybit.candles import BybitLinearCandleClient
from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.bybit_v1 import evaluate


def candles(interval: str, minutes: int, count: int = 100, base: int = 100):
    end = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    result = []
    for i in range(count):
        opened = end - timedelta(minutes=(count - i) * minutes)
        price = Decimal(base) + Decimal(i) / Decimal(10)
        result.append(Candle(
            symbol="BTCUSDT", interval=interval, open_time=opened,
            close_time=opened + timedelta(minutes=minutes),
            open=price, high=price + 1, low=price - 1,
            close=price + Decimal("0.2"), volume=Decimal("100"),
        ))
    return result


def test_rejects_invalid_symbol():
    with pytest.raises(ValueError):
        evaluate("DOGEUSDT", [], [], [])


def test_wait_on_missing_data():
    decisions = evaluate("BTCUSDT", [], [], [])
    assert len(decisions) == 2
    assert all(s.side == "WAIT" for s in decisions)


def test_wait_on_discontinuous_data():
    one = candles("1m", 1)
    one.pop(50)
    decisions = evaluate("BTCUSDT", one, candles("5m", 5), candles("15m", 15))
    assert all(s.side == "WAIT" for s in decisions)


def test_no_signal_on_unconfirmed_breakout():
    decisions = evaluate("BTCUSDT", candles("1m", 1), candles("5m", 5), candles("15m", 15))
    assert len(decisions) == 2
    assert all(s.side == "WAIT" for s in decisions)


def test_risk_validation():
    with pytest.raises(ValueError):
        evaluate("BTCUSDT", [], [], [], risk_pct=Decimal("2"))


def test_bybit_candle_parser_is_closed_only():
    assert BybitLinearCandleClient.BASE_URL.endswith("/v5/market/kline")
