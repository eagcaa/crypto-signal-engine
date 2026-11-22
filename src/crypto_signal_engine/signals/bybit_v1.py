"""Research-only Bybit perpetual signals. Never submits orders."""
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.features.technical import build_technical_features

D = Decimal


@dataclass(frozen=True)
class Signal:
    symbol: str
    strategy: str
    side: str
    reason: str
    entry: Decimal | None = None
    stop: Decimal | None = None
    take_profit: Decimal | None = None
    leverage: int | None = None
    notional: Decimal | None = None


def _valid(candles: list[Candle], interval: str, symbol: str) -> bool:
    minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}[interval]
    if len(candles) < 90 or any(not c.closed or c.interval != interval or c.symbol != symbol for c in candles):
        return False
    return all(
        (b.open_time - a.open_time).total_seconds() == minutes * 60
        and a.close_time <= b.open_time
        and a.low <= min(a.open, a.close) <= max(a.open, a.close) <= a.high
        and a.volume >= 0
        for a, b in zip(candles, candles[1:])
    )


def evaluate(
    symbol: str, candles_1m: list[Candle], candles_5m: list[Candle],
    candles_15m: list[Candle], *, equity: Decimal = D("1000"),
    risk_pct: Decimal = D("0.5"), max_notional_pct: Decimal = D("50"),
    fee_pct_per_side: Decimal = D("0.055"),
    slippage_pct_per_side: Decimal = D("0.02"),
) -> list[Signal]:
    """Closed candles only; returns two independent strategy decisions."""
    if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
        raise ValueError("Unsupported symbol")
    if equity <= 0 or not D("0") < risk_pct <= D("1") or not D("0") < max_notional_pct <= D("100"):
        raise ValueError("Invalid risk settings")
    if min(fee_pct_per_side, slippage_pct_per_side) < 0:
        raise ValueError("Costs cannot be negative")
    wait = lambda strategy, reason: Signal(symbol, strategy, "WAIT", reason)
    if not all(_valid(rows, tf, symbol) for rows, tf in (
        (candles_1m, "1m"), (candles_5m, "5m"), (candles_15m, "15m")
    )):
        return [wait("trend", "Insufficient or discontinuous closed candles"),
                wait("retest", "Insufficient or discontinuous closed candles")]
    if not (candles_1m[-1].close_time == candles_5m[-1].close_time == candles_15m[-1].close_time):
        return [wait("trend", "Timeframes not aligned"), wait("retest", "Timeframes not aligned")]

    t15 = build_technical_features(candles_15m, fast_period=20, slow_period=50)
    t5 = build_technical_features(candles_5m, fast_period=20, slow_period=50)
    price = candles_1m[-1].close
    side = "LONG" if t15.ema_fast > t15.ema_slow and t15.close > t15.ema_fast else (
        "SHORT" if t15.ema_fast < t15.ema_slow and t15.close < t15.ema_fast else None
    )
    if side is None or t5.trend_regime == "range" or (
        side == "LONG" and t5.ema_fast <= t5.ema_slow
    ) or (side == "SHORT" and t5.ema_fast >= t5.ema_slow):
        return [wait("trend", "15m/5m trend not aligned"), wait("retest", "15m/5m trend not aligned")]

    previous = candles_5m[-21:-1]
    resistance = max(c.high for c in previous)
    support = min(c.low for c in previous)
    average_volume = sum((c.volume for c in previous), D("0")) / D(len(previous))
    last = candles_5m[-1]
    breakout = (last.close > resistance if side == "LONG" else last.close < support)
    volume_ok = last.volume >= average_volume * D("1.3") and average_volume > 0
    one_minute_confirm = (
        candles_1m[-1].close > candles_1m[-1].open if side == "LONG"
        else candles_1m[-1].close < candles_1m[-1].open
    )

    def make(strategy: str, confirmed: bool, reason: str) -> Signal:
        if not confirmed:
            return wait(strategy, reason)
        atr = t5.atr
        stop = (min(last.low, price - atr * D("1.5")) if side == "LONG"
                else max(last.high, price + atr * D("1.5")))
        distance = abs(price - stop)
        if price <= 0 or distance <= 0:
            return wait(strategy, "Invalid ATR/stop")
        cost_fraction = (fee_pct_per_side + slippage_pct_per_side) * D("2") / D("100")
        loss_fraction = distance / price + cost_fraction
        notional = min(equity * risk_pct / D("100") / loss_fraction,
                       equity * max_notional_pct / D("100"))
        if notional <= 0:
            return wait(strategy, "No risk budget")
        target_distance = distance * D("2") + price * cost_fraction * D("3")
        target = price + target_distance if side == "LONG" else price - target_distance
        if target <= 0:
            return wait(strategy, "Invalid target")
        # Leverage is a margin choice, not a substitute for position sizing.
        stop_pct = distance / price * D("100")
        leverage = 3 if stop_pct >= D("1") else 5
        return Signal(symbol, strategy, side, reason, price, stop, target, leverage, notional)

    trend = make("trend", breakout and volume_ok and one_minute_confirm,
                 "5m breakout, 1.3x volume, 1m confirmation")
    # Retest requires a prior confirmed breakout and current test of its level.
    older = candles_5m[-22:-2]
    prior = candles_5m[-2]
    level = max(c.high for c in older) if side == "LONG" else min(c.low for c in older)
    prior_break = prior.close > level if side == "LONG" else prior.close < level
    tolerance = t5.atr * D("0.25")
    retested = (
        last.low <= level + tolerance and last.close > level if side == "LONG"
        else last.high >= level - tolerance and last.close < level
    )
    retest = make("retest", prior_break and retested and one_minute_confirm,
                  "Previous 5m breakout followed by level retest and 1m confirmation")
    return [trend, retest]
