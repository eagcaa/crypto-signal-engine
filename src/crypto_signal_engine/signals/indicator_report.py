"""Explainable indicator snapshot for closed Bybit perpetual candles.

This is a market bias, NOT a trade entry or order instruction.
"""
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle

D = Decimal


def ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("Insufficient EMA history")
    value = sum(values[:period], D(0)) / D(period)
    alpha = D(2) / D(period + 1)
    for close in values[period:]:
        value += alpha * (close - value)
    return value


def kdj(candles: list[Candle], period: int = 9) -> tuple[Decimal, Decimal, Decimal]:
    if len(candles) < period + 1:
        raise ValueError("Insufficient KDJ history")
    k = d = D(50)
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1:i + 1]
        low, high = min(c.low for c in window), max(c.high for c in window)
        rsv = (candles[i].close - low) * 100 / (high - low) if high > low else D(50)
        k = (D(2) * k + rsv) / 3
        d = (D(2) * d + k) / 3
    return k, d, D(3) * k - D(2) * d


@dataclass(frozen=True)
class IndicatorReport:
    symbol: str
    interval: str
    close_time: str
    price: Decimal
    ma20: Decimal
    ma50: Decimal
    macd: Decimal
    macd_signal: Decimal
    macd_histogram: Decimal
    k: Decimal
    d: Decimal
    j: Decimal
    volume_ratio: Decimal
    pivot: Decimal
    support1: Decimal
    resistance1: Decimal
    bias: str
    explanation: str


def analyze(candles: list[Candle]) -> IndicatorReport:
    if len(candles) < 90:
        raise ValueError("At least 90 closed candles required")
    symbol, interval = candles[-1].symbol, candles[-1].interval
    minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(interval)
    if minutes is None:
        raise ValueError("Unsupported interval")
    for i, c in enumerate(candles):
        if not c.closed or c.symbol != symbol or c.interval != interval:
            raise ValueError("Mixed or open candles")
        if c.volume < 0 or c.low > min(c.open, c.close) or c.high < max(c.open, c.close):
            raise ValueError("Invalid candle")
        if i and (c.open_time - candles[i - 1].open_time).total_seconds() != minutes * 60:
            raise ValueError("Discontinuous candles")
    closes = [c.close for c in candles]
    ma20 = sum(closes[-20:], D(0)) / 20
    ma50 = sum(closes[-50:], D(0)) / 50
    macd_series = [ema(closes[:i], 12) - ema(closes[:i], 26)
                   for i in range(26, len(closes) + 1)]
    macd = macd_series[-1]
    macd_signal = ema(macd_series, 9)
    k, d, j = kdj(candles)
    volume_base = sum((c.volume for c in candles[-21:-1]), D(0)) / 20
    volume_ratio = candles[-1].volume / volume_base if volume_base > 0 else D(0)
    # Previous closed candle's classic pivot; not a daily/session pivot.
    previous = candles[-2]
    pivot = (previous.high + previous.low + previous.close) / 3
    support1 = D(2) * pivot - previous.high
    resistance1 = D(2) * pivot - previous.low
    price = closes[-1]
    trend = "UP" if price > ma20 > ma50 else "DOWN" if price < ma20 < ma50 else "MIXED"
    momentum = "UP" if macd > macd_signal and macd > 0 else (
        "DOWN" if macd < macd_signal and macd < 0 else "MIXED")
    kdj_direction = "UP" if k > d else "DOWN" if k < d else "MIXED"
    # Avoid treating correlated indicators as three independent votes.
    bias = ("LONG_BIAS" if trend == momentum == "UP" and kdj_direction == "UP"
            else "SHORT_BIAS" if trend == momentum == "DOWN" and kdj_direction == "DOWN"
            else "NEUTRAL")
    explanation = (f"MA trend={trend}; MACD momentum={momentum}; "
                   f"KDJ direction={kdj_direction}; volume={volume_ratio:.2f}x; "
                   f"price {'above' if price > pivot else 'below' if price < pivot else 'at'} previous-candle pivot. "
                   "Bias only; not an entry signal.")
    return IndicatorReport(symbol, interval, candles[-1].close_time.isoformat(),
                           price, ma20, ma50, macd, macd_signal, macd - macd_signal,
                           k, d, j, volume_ratio, pivot, support1, resistance1,
                           bias, explanation)
