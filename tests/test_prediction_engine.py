from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.market import MarketSnapshot
from crypto_signal_engine.predictions import (
    BaselinePredictionEngine,
    PredictionDirection,
)


def make_snapshot(
    *,
    binance_imbalance: str,
    bybit_imbalance: str,
    data_quality: str = "0.83",
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 6, 19, 0, tzinfo=UTC),
        price=Decimal("85500"),
        binance_spot_cvd=Decimal("1"),
        binance_futures_cvd=None,
        bybit_spot_cvd=Decimal("1"),
        bybit_futures_cvd=Decimal("1"),
        binance_book_imbalance=Decimal(binance_imbalance),
        bybit_book_imbalance=Decimal(bybit_imbalance),
        spot_cvd_total=Decimal("2"),
        futures_cvd_total=None,
        spot_futures_divergence=None,
        cross_exchange_book_divergence=Decimal("0"),
        buy_pressure=Decimal("0"),
        sell_pressure=Decimal("0"),
        binance_spot_age_ms=100,
        binance_futures_age_ms=None,
        bybit_spot_age_ms=100,
        bybit_futures_age_ms=100,
        binance_book_age_ms=100,
        bybit_book_age_ms=100,
        data_quality=Decimal(data_quality),
    )


def test_generates_long_when_book_pressure_is_strongly_positive() -> None:
    prediction = BaselinePredictionEngine().generate(
        make_snapshot(
            binance_imbalance="0.70",
            bybit_imbalance="0.50",
        ),
        horizon_seconds=300,
    )

    assert prediction is not None
    assert prediction.direction == PredictionDirection.LONG
    assert prediction.raw_score == Decimal("0.60")
    assert prediction.horizon_seconds == 300


def test_generates_short_when_book_pressure_is_strongly_negative() -> None:
    prediction = BaselinePredictionEngine().generate(
        make_snapshot(
            binance_imbalance="-0.80",
            bybit_imbalance="-0.40",
        ),
        horizon_seconds=900,
    )

    assert prediction is not None
    assert prediction.direction == PredictionDirection.SHORT
    assert prediction.raw_score == Decimal("-0.60")


def test_skips_weak_or_low_quality_snapshots() -> None:
    engine = BaselinePredictionEngine()

    assert engine.generate(
        make_snapshot(
            binance_imbalance="0.10",
            bybit_imbalance="0.20",
        ),
        horizon_seconds=300,
    ) is None

    assert engine.generate(
        make_snapshot(
            binance_imbalance="0.80",
            bybit_imbalance="0.60",
            data_quality="0.50",
        ),
        horizon_seconds=300,
    ) is None
