from dataclasses import replace
from decimal import Decimal

from crypto_signal_engine.signals.direction_forecast import forecast
from crypto_signal_engine.signals.indicator_report import IndicatorReport

D = Decimal


def report(**changes):
    base = IndicatorReport(
        symbol="BTCUSDT", interval="5m", close_time="2026-01-01T00:00:00+00:00",
        price=D(110), ma20=D(105), ma50=D(100),
        macd=D(2), macd_signal=D(1), macd_histogram=D(1),
        k=D(70), d=D(60), j=D(90), volume_ratio=D("1.5"),
        pivot=D(108), support1=D(104), resistance1=D(112),
        bias="LONG_BIAS", explanation="test")
    return replace(base, **changes)


def test_up_direction():
    result = forecast(report())
    assert result.direction == "RISE_EXPECTED"
    assert result.strength == "STRONG"


def test_down_direction():
    result = forecast(report(price=D(90), ma20=D(95), ma50=D(100),
                             macd=D(-2), macd_signal=D(-1),
                             k=D(30), d=D(40), pivot=D(92)))
    assert result.direction == "FALL_EXPECTED"


def test_conflicting_trend_is_uncertain():
    result = forecast(report(macd=D(-2), macd_signal=D(-1)))
    assert result.direction == "UNCERTAIN"


def test_oscillator_and_pivot_cannot_override_trend():
    result = forecast(report(k=D(30), d=D(40), pivot=D(115)))
    assert result.direction == "UNCERTAIN"


def test_weak_volume_not_strong():
    result = forecast(report(volume_ratio=D("0.4")))
    assert result.direction == "RISE_EXPECTED"
    assert result.strength == "MODERATE"
