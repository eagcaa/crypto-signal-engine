from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle


@dataclass(frozen=True, slots=True)
class TechnicalFeatureSnapshot:
    interval: str
    close: Decimal
    ema_fast: Decimal
    ema_slow: Decimal
    ema_spread_atr: Decimal
    atr: Decimal
    atr_pct: Decimal
    trend_score: Decimal
    trend_regime: str
    volatility_regime: str


def _ema(values: list[Decimal], period: int) -> Decimal:
    if not values:
        raise ValueError("EMA requires at least one value")

    alpha = Decimal("2") / Decimal(period + 1)
    value = values[0]
    for item in values[1:]:
        value = (item * alpha) + (value * (Decimal("1") - alpha))
    return value


def _true_ranges(candles: list[Candle]) -> list[Decimal]:
    if len(candles) < 2:
        return []

    ranges: list[Decimal] = []
    previous_close = candles[0].close

    for candle in candles[1:]:
        ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close

    return ranges


def _simple_average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def build_technical_features(
    candles: list[Candle],
    *,
    fast_period: int = 9,
    slow_period: int = 21,
    atr_period: int = 14,
) -> TechnicalFeatureSnapshot:
    required = max(slow_period + 1, atr_period + 2)
    if len(candles) < required:
        raise ValueError(
            f"At least {required} closed candles are required; got {len(candles)}"
        )

    interval = candles[-1].interval
    if any(candle.interval != interval for candle in candles):
        raise ValueError("All candles must use the same interval")

    closes = [candle.close for candle in candles]
    ema_fast = _ema(closes[-max(fast_period * 4, fast_period):], fast_period)
    ema_slow = _ema(closes[-max(slow_period * 4, slow_period):], slow_period)

    true_ranges = _true_ranges(candles)
    atr = _simple_average(true_ranges[-atr_period:])
    close = closes[-1]
    atr_pct = (
        (atr / close) * Decimal("100")
        if close != 0
        else Decimal("0")
    )

    if atr == 0:
        ema_spread_atr = Decimal("0")
    else:
        ema_spread_atr = (ema_fast - ema_slow) / atr

    trend_score = max(
        Decimal("-1"),
        min(Decimal("1"), ema_spread_atr),
    )

    if trend_score >= Decimal("0.20"):
        trend_regime = "uptrend"
    elif trend_score <= Decimal("-0.20"):
        trend_regime = "downtrend"
    else:
        trend_regime = "range"

    rolling_atrs: list[Decimal] = []
    history_start = max(atr_period + 1, len(candles) - 50)
    for end in range(history_start, len(candles) + 1):
        sample_ranges = _true_ranges(candles[:end])
        if len(sample_ranges) >= atr_period:
            rolling_atrs.append(
                _simple_average(sample_ranges[-atr_period:])
            )

    baseline_atr = _median(rolling_atrs)
    if baseline_atr == 0:
        volatility_regime = "normal"
    else:
        ratio = atr / baseline_atr
        if ratio >= Decimal("1.35"):
            volatility_regime = "high"
        elif ratio <= Decimal("0.75"):
            volatility_regime = "low"
        else:
            volatility_regime = "normal"

    return TechnicalFeatureSnapshot(
        interval=interval,
        close=close,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_spread_atr=ema_spread_atr,
        atr=atr,
        atr_pct=atr_pct,
        trend_score=trend_score,
        trend_regime=trend_regime,
        volatility_regime=volatility_regime,
    )
