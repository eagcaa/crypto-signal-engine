from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.collectors.bybit.perp_orderbook import PerpOrderBook
from crypto_signal_engine.signals.combined_forecast import combine_forecast
from crypto_signal_engine.signals.direction_forecast import DirectionForecast

NOW = datetime(2026, 11, 22, 14, 30, tzinfo=UTC)


def prediction(direction="FALL_EXPECTED"):
    return DirectionForecast(direction, "MODERATE", (), (), "test")


def book(pressure="BUY_PRESSURE", timestamp=NOW):
    return PerpOrderBook("BTCUSDT", timestamp, Decimal(100), Decimal(101),
                         Decimal(99), Decimal(1000), Decimal(500),
                         Decimal("0.333"), pressure)


def test_conflicting_buy_pressure_for_downtrend():
    result = combine_forecast(prediction(), book(), now=NOW)
    assert result.direction == "FALL_EXPECTED"
    assert result.confirmation == "CONFLICT"


def test_matching_sell_pressure():
    result = combine_forecast(prediction(), book("SELL_PRESSURE"), now=NOW)
    assert result.confirmation == "ALIGNED"


def test_balanced_book():
    result = combine_forecast(prediction(), book("BALANCED"), now=NOW)
    assert result.confirmation == "BALANCED"


def test_stale_book_is_not_confirmation():
    result = combine_forecast(prediction(), book(timestamp=NOW - timedelta(seconds=40)), now=NOW)
    assert result.confirmation == "ORDERBOOK_STALE"


def test_missing_book_is_not_confirmation():
    assert combine_forecast(prediction(), None, now=NOW).confirmation == "ORDERBOOK_UNAVAILABLE"


def test_no_direction_is_not_created_by_book():
    result = combine_forecast(prediction("UNCERTAIN"), book("SELL_PRESSURE"), now=NOW)
    assert result.direction == "UNCERTAIN"
    assert result.confirmation == "NO_TECHNICAL_DIRECTION"


def test_rise_and_buy_pressure_agree():
    assert combine_forecast(prediction("RISE_EXPECTED"), book(), now=NOW).confirmation == "ALIGNED"
