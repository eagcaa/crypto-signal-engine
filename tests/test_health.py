from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_engine.health import DataQualityMonitor
from crypto_signal_engine.market.snapshot import MarketSnapshot


def make_snapshot(quality: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        price=Decimal("100"),
        binance_spot_cvd=Decimal("1"),
        binance_futures_cvd=Decimal("1"),
        bybit_spot_cvd=Decimal("1"),
        bybit_futures_cvd=Decimal("1"),
        binance_book_imbalance=Decimal("0"),
        bybit_book_imbalance=Decimal("0"),
        spot_cvd_total=Decimal("2"),
        futures_cvd_total=Decimal("2"),
        spot_futures_divergence=Decimal("0"),
        cross_exchange_book_divergence=Decimal("0"),
        buy_pressure=Decimal("0"),
        sell_pressure=Decimal("0"),
        binance_spot_age_ms=1,
        binance_futures_age_ms=1,
        bybit_spot_age_ms=1,
        bybit_futures_age_ms=1,
        binance_book_age_ms=1,
        bybit_book_age_ms=1,
        data_quality=Decimal(quality),
    )


def test_monitor_alerts_once_after_consecutive_bad_intervals() -> None:
    monitor = DataQualityMonitor(
        minimum_quality=Decimal("0.67"),
        bad_intervals_before_alert=3,
    )

    assert monitor.observe(make_snapshot("0.50")) is None
    assert monitor.observe(make_snapshot("0.50")) is None

    event = monitor.observe(make_snapshot("0.50"))

    assert event is not None
    assert event.kind == "degraded"
    assert event.bad_intervals == 3

    assert monitor.observe(make_snapshot("0.33")) is None


def test_monitor_emits_recovery_transition() -> None:
    monitor = DataQualityMonitor(
        minimum_quality=Decimal("0.67"),
        bad_intervals_before_alert=2,
    )

    monitor.observe(make_snapshot("0.50"))
    degraded = monitor.observe(make_snapshot("0.50"))
    recovered = monitor.observe(make_snapshot("0.83"))

    assert degraded is not None
    assert recovered is not None
    assert recovered.kind == "recovered"
    assert recovered.data_quality == Decimal("0.83")


def test_monitor_validates_configuration() -> None:
    with pytest.raises(ValueError):
        DataQualityMonitor(minimum_quality=Decimal("1.1"))

    with pytest.raises(ValueError):
        DataQualityMonitor(bad_intervals_before_alert=0)
