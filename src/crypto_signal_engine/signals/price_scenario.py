"""Experimental, uncalibrated 5/15-minute price scenarios from closed 1m candles.

This is a deterministic baseline, not a trained predictor or trading signal.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from crypto_signal_engine.domain.candles import Candle

D = Decimal


@dataclass(frozen=True)
class PriceScenario:
    symbol: str
    observed_at: str
    current: Decimal
    horizon_minutes: int
    estimate: Decimal
    lower: Decimal
    upper: Decimal
    expected_change_pct: Decimal


def scenarios(candles: list[Candle], horizons=(5, 15)) -> list[PriceScenario]:
    if len(candles) < 60:
        raise ValueError("At least 60 closed 1m candles required")
    recent = candles[-60:]
    if any(c.interval != "1m" or c.symbol != recent[0].symbol or not c.closed for c in recent):
        raise ValueError("Only closed, single-symbol 1m candles supported")
    if any((b.open_time - a.open_time).total_seconds() != 60
           for a, b in zip(recent, recent[1:])):
        raise ValueError("Candles must be continuous")
    closes = [c.close for c in recent]
    if any(p <= 0 for p in closes):
        raise ValueError("Prices must be positive")
    current = closes[-1]
    # Simple 15-minute observed return, conservatively damped to 25%.
    # Avoid extrapolating an entire short-lived move indefinitely.
    drift_per_min = ((current / closes[-16]) - 1) / D(15) * D("0.25")
    # ATR(14) measures observed one-minute range; sqrt(time) is a rough
    # scenario scaling, NOT a statistically calibrated confidence interval.
    true_ranges = [
        max(c.high - c.low, abs(c.high - previous.close), abs(c.low - previous.close))
        for previous, c in zip(recent[-15:-1], recent[-14:])
    ]
    atr = sum(true_ranges, D(0)) / D(len(true_ranges))
    results = []
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("horizon must be positive")
        estimate = current * (1 + drift_per_min * D(horizon))
        half_width = atr * D(str(sqrt(horizon)))
        results.append(PriceScenario(
            recent[-1].symbol, recent[-1].close_time.isoformat(), current,
            horizon, estimate, max(D(0), estimate - half_width),
            estimate + half_width, (estimate / current - 1) * 100))
    return results
