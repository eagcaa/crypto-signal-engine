from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.db.repository import MarketSnapshotRepository
from crypto_signal_engine.market import MarketSnapshot


def test_market_snapshot_maps_to_database_row() -> None:
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 6, 19, 0, tzinfo=UTC),
        price=Decimal("85500.1"),
        binance_spot_cvd=Decimal("1.2"),
        binance_futures_cvd=None,
        bybit_spot_cvd=Decimal("-0.2"),
        bybit_futures_cvd=Decimal("0.5"),
        binance_book_imbalance=Decimal("-0.4"),
        bybit_book_imbalance=Decimal("-0.1"),
        spot_cvd_total=Decimal("1.0"),
        futures_cvd_total=None,
        spot_futures_divergence=None,
        cross_exchange_book_divergence=Decimal("-0.3"),
        buy_pressure=Decimal("0"),
        sell_pressure=Decimal("0.25"),
        binance_spot_age_ms=100,
        binance_futures_age_ms=None,
        bybit_spot_age_ms=200,
        bybit_futures_age_ms=300,
        binance_book_age_ms=50,
        bybit_book_age_ms=60,
        data_quality=Decimal("0.833333"),
    )

    row = MarketSnapshotRepository.to_row(snapshot)

    assert row.symbol == "BTCUSDT"
    assert row.price == Decimal("85500.1")
    assert row.binance_futures_cvd is None
    assert row.sell_pressure == Decimal("0.25")
    assert row.data_quality == Decimal("0.833333")
