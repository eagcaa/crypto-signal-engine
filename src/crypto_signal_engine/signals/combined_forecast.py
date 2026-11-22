"""Combine closed-candle direction with a fresh perpetual order-book snapshot.

Order-book depth is displayed liquidity, not executed trade flow. No orders.
"""
from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_signal_engine.collectors.bybit.perp_orderbook import PerpOrderBook
from crypto_signal_engine.signals.direction_forecast import DirectionForecast


@dataclass(frozen=True)
class CombinedForecast:
    direction: str
    confirmation: str
    explanation: str


def combine_forecast(
    prediction: DirectionForecast,
    book: PerpOrderBook | None,
    *,
    now: datetime | None = None,
    max_age_seconds: float = 30.0,
) -> CombinedForecast:
    if prediction.direction == "UNCERTAIN":
        return CombinedForecast("UNCERTAIN", "NO_TECHNICAL_DIRECTION",
                                "Teknik gostergeler net yon vermiyor; order book tek basina tahmin olusturmaz.")
    if book is None:
        return CombinedForecast(prediction.direction, "ORDERBOOK_UNAVAILABLE",
                                "Teknik yon beklentisi var, order book verisi yok; teyit edilmedi.")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = (current - book.timestamp).total_seconds()
    if age < -5 or age > max_age_seconds:
        return CombinedForecast(prediction.direction, "ORDERBOOK_STALE",
                                f"Order book zaman uyumsuz veya eski ({age:.1f}s); teyit edilmedi.")
    expected_pressure = ("BUY_PRESSURE" if prediction.direction == "RISE_EXPECTED"
                         else "SELL_PRESSURE")
    opposite_pressure = ("SELL_PRESSURE" if expected_pressure == "BUY_PRESSURE"
                         else "BUY_PRESSURE")
    if book.pressure == expected_pressure:
        confirmation = "ALIGNED"
        detail = "Order book gorunen likiditesi teknik beklentiyi destekliyor."
    elif book.pressure == opposite_pressure:
        confirmation = "CONFLICT"
        detail = "Order book gorunen likiditesi teknik beklentiye ters; islem onayi yok."
    else:
        confirmation = "BALANCED"
        detail = "Order book dengeli; teknik beklenti icin ek teyit yok."
    return CombinedForecast(prediction.direction, confirmation,
                            f"{detail} Imbalance={book.imbalance:+.2%}; "
                            "tek snapshot, gerceklesen alim/satim akisi degildir.")
