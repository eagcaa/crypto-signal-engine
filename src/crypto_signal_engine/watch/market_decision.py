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

    positives: list[str] = []
    negatives: list[str] = []
    if price > ema20 > ema50 > ema200:
        positives.append("Fiyat ve EMA20/50/200 yükseliş sıralamasında")
    elif price < ema20 < ema50 < ema200:
        negatives.append("Fiyat ve EMA20/50/200 düşüş sıralamasında")
    else:
        positives.append("EMA dizilimi karışık: trend teyidi zayıf")

    if 52 <= rsi <= 70:
        positives.append(f"15dk RSI {rsi:.1f}: pozitif momentum")
    elif 30 <= rsi <= 48:
        negatives.append(f"15dk RSI {rsi:.1f}: negatif momentum")
    elif rsi > 70:
        negatives.append(f"15dk RSI {rsi:.1f}: aşırı alım riski")
    elif rsi < 30:
        positives.append(f"15dk RSI {rsi:.1f}: aşırı satım, dönüş teyidi gerekli")

    flows = {row[0]: row for row in flow_rows}
    hour_flow = flows.get("1s")
    if hour_flow is not None and hour_flow[5] is not None:
        buy_share = hour_flow[5]
        if buy_share >= 55:
            positives.append(f"Spot taker alım payı %{buy_share:.0f}")
        elif buy_share <= 45:
            negatives.append(f"Spot taker alım payı %{buy_share:.0f}")

    oi = _float(getattr(derivatives, "oi_change_15m_pct", None))
    if oi is not None:
        if abs(oi) >= 1:
            positives.append(f"OI 15dk %{oi:+.2f}: pozisyon değişimi (yönü tek başına belirlemez)")
    funding = _float(getattr(derivatives, "funding_rate", None))
    if funding is not None and abs(funding) >= 0.0005:
        negatives.append(f"Funding %{funding * 100:+.3f}: kaldıraç yoğunluğu riski")

    bullish = price > ema20 > ema50 > ema200 and 52 <= rsi <= 70 and (
        hour_flow is not None and hour_flow[5] is not None and hour_flow[5] >= 55
    )
    bearish = price < ema20 < ema50 < ema200 and 30 <= rsi <= 48 and (
        hour_flow is not None and hour_flow[5] is not None and hour_flow[5] <= 45
    )
    state = "Yukarı eğilim / teyit gerekli" if bullish else (
        "Aşağı eğilim / teyit gerekli" if bearish else "Kararsız / işlem için teyit yok"
    )
    last = fifteen_minute[-20:]
    resistance = max(float(c.high) for c in last)
    support = min(float(c.low) for c in last)
    lines = [
        "", "🧭 PİYASA KARAR DESTEĞİ (deneysel)",
        f"Durum: {state}",
        f"15dk RSI: {rsi:.1f} | ATR: {atr:.6g} ({atr / price * 100:.2f}%)",
        f"EMA20: {ema20:.6g} | EMA50: {ema50:.6g} | EMA200: {ema200:.6g}",
        f"Son 20 kapanmış 15dk mum: destek ~{support:.6g} / direnç ~{resistance:.6g}",
        "Yukarı senaryo: direnç üzerinde kapanış + hacim teyidi.",
        "Aşağı senaryo: destek altında kapanış + satış hacmi teyidi.",
        "Geçersizlik: kırılımın tekrar bant içine dönmesi.",
        "İşlem kararı: BEKLE — giriş, stop ve hedef için ayrı backtest şart.",
        "Yön olasılığı: kalibre edilmiş model yok; yüzde verilmez.",
        "Destekleyen gözlemler: " + ("; ".join(positives) if positives else "yok"),
        "Risk / karşı kanıt: " + ("; ".join(negatives) if negatives else "belirgin veri yok"),
        "Not: Bu analiz finansal tavsiye veya otomatik al/sat emri değildir.",
    ]
    return "\n".join(lines)
