"""Directional forecast based on closed-candle indicator evidence.

A forecast is not an order signal. Agreement is NOT a calibrated probability.
"""
from dataclasses import dataclass

from crypto_signal_engine.signals.indicator_report import IndicatorReport


@dataclass(frozen=True)
class DirectionForecast:
    direction: str
    strength: str
    supporting: tuple[str, ...]
    opposing: tuple[str, ...]
    explanation: str


def forecast(report: IndicatorReport) -> DirectionForecast:
    price = report.price
    ma_up = price > report.ma20 > report.ma50
    ma_down = price < report.ma20 < report.ma50
    macd_up = report.macd > report.macd_signal and report.macd > 0
    macd_down = report.macd < report.macd_signal and report.macd < 0
    kdj_up = report.k > report.d
    kdj_down = report.k < report.d
    pivot_up = price > report.pivot
    pivot_down = price < report.pivot

    # Group correlated moving-average and MACD evidence into one trend vote.
    trend_up = ma_up and macd_up
    trend_down = ma_down and macd_down
    votes = [
        ("MA+MACD trend", 2 if trend_up else -2 if trend_down else 0),
        ("KDJ momentum", 1 if kdj_up else -1 if kdj_down else 0),
        ("Previous-candle pivot", 1 if pivot_up else -1 if pivot_down else 0),
    ]
    score = sum(value for _, value in votes)
    # Require a confirmed trend: oscillator + pivot alone cannot dictate direction.
    if trend_up and score >= 3:
        direction = "RISE_EXPECTED"
    elif trend_down and score <= -3:
        direction = "FALL_EXPECTED"
    else:
        direction = "UNCERTAIN"

    support_sign = 1 if direction == "RISE_EXPECTED" else -1 if direction == "FALL_EXPECTED" else 0
    supporting = tuple(name for name, value in votes if support_sign and value * support_sign > 0)
    opposing = tuple(name for name, value in votes if support_sign and value * support_sign < 0)
    strong_volume = report.volume_ratio >= 1.3
    strength = ("STRONG" if direction != "UNCERTAIN" and abs(score) == 4 and strong_volume
                else "MODERATE" if direction != "UNCERTAIN"
                else "NONE")
    explanation = (
        f"direction={direction}; score={score}/4 (heuristic, not probability); "
        f"volume={report.volume_ratio:.2f}x "
        f"({'confirming' if strong_volume else 'not confirming'}); "
        f"support={', '.join(supporting) or 'none'}; "
        f"opposition={', '.join(opposing) or 'none'}. "
        "Closed-candle directional expectation only; no entry or guaranteed outcome."
    )
    return DirectionForecast(direction, strength, supporting, opposing, explanation)
