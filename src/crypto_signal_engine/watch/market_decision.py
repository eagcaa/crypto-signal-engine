"""Explainable, non-executing market decision support for Telegram.

This is a rules-based evidence summary, NOT a calibrated probability model.
It deliberately refuses to issue an actionable trade when confirmation is weak.
"""
from __future__ import annotations

from math import isfinite


def _float(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    weight = 2 / (period + 1)
    for item in values[period:]:
        value = item * weight + value * (1 - weight)
    return value


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(candles, period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    recent = candles[-(period + 1):]
    trs = []
    for previous, current in zip(recent, recent[1:]):
        high, low, prev_close = float(current.high), float(current.low), float(previous.close)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs)


def market_decision_report(
    symbol: str,
    candle_sets,
    derivatives=None,
    flow_rows=(),
    ranges=(),
) -> str:
    """Use only closed candles and observed fields; never manufacture missing data."""
    if len(candle_sets) < 4 or not candle_sets[0] or not candle_sets[2]:
        return "🧭 Karar desteği: yeterli mum verisi yok."
    minute, five_minute, fifteen_minute, hourly = candle_sets
    closes = [float(c.close) for c in fifteen_minute]
    price = float(minute[-1].close)
    ema20, ema50, ema200 = (_ema(closes, n) for n in (20, 50, 200))
    rsi = _rsi(closes)
    atr = _atr(fifteen_minute)
    if not all(x is not None for x in (ema20, ema50, ema200, rsi, atr)):
        return "🧭 Karar desteği: indikatör hesaplamak için yeterli geçmiş yok."

    notes: list[str] = []
    risks: list[str] = []
    if price > ema20 > ema50 > ema200:
        notes.append("EMA sıralaması yükseliş yönünde (tahmin değil)")
    elif price < ema20 < ema50 < ema200:
        notes.append("EMA sıralaması düşüş yönünde (tahmin değil)")
    else:
        risks.append("EMA sıralaması karışık; trend teyidi zayıf")

    if rsi > 70:
        risks.append(f"RSI {rsi:.1f}: aşırı alım")
    elif rsi < 30:
        risks.append(f"RSI {rsi:.1f}: aşırı satım")
    else:
        notes.append(f"RSI {rsi:.1f}: momentum ölçümü")

    flows = {row[0]: row for row in flow_rows}
    hour_flow = flows.get("1s")
    if hour_flow is not None and hour_flow[5] is not None:
        notes.append(f"Son 1 saat spot taker alım payı %{hour_flow[5]:.0f}")

    oi = _float(getattr(derivatives, "oi_change_15m_pct", None))
    if oi is not None:
        notes.append(f"OI 15dk %{oi:+.2f} (yön belirtmez)")
    funding = _float(getattr(derivatives, "funding_rate", None))
    if funding is not None and abs(funding) >= 0.0005:
        risks.append(f"Funding %{funding * 100:+.3f}: kaldıraç yoğunluğu")

    last = fifteen_minute[-20:]
    resistance = max(float(c.high) for c in last)
    support = min(float(c.low) for c in last)
    lines = [
        "", "🧭 PİYASA GÖSTERGELERİ (yön sinyali değildir)",
        f"15dk RSI: {rsi:.1f} | ATR: {atr:.6g} (%{atr / price * 100:.2f})",
        f"EMA20: {ema20:.6g} | EMA50: {ema50:.6g} | EMA200: {ema200:.6g}",
        f"Son 20 kapanmış 15dk mum: düşük {support:.6g} / yüksek {resistance:.6g}",
        "Gözlemler: " + "; ".join(notes),
    ]
    if risks:
        lines.append("Riskler: " + "; ".join(risks))
    lines.append("Yön kuralı henüz geçmiş veride doğrulanmadı.")
    return "\n".join(lines)
