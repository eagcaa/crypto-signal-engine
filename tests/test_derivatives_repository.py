from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.db.derivatives_repository import DerivativesRepository
from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
)
from crypto_signal_engine.domain.models import Exchange


def test_derivatives_snapshot_fields_are_serializable() -> None:
    snapshot = ExchangeDerivativesSnapshot(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 6, 20, 0, tzinfo=UTC),
        open_interest=Decimal("123"),
        open_interest_value_usd=Decimal("1000000"),
        oi_change_5m_pct=Decimal("1.5"),
        oi_change_15m_pct=Decimal("2.5"),
        funding_rate=Decimal("0.0001"),
        long_short_ratio=Decimal("1.1"),
        long_account_ratio=Decimal("0.52"),
        short_account_ratio=Decimal("0.48"),
        top_trader_long_short_ratio=Decimal("1.2"),
        taker_buy_sell_ratio=Decimal("1.3"),
        taker_buy_volume=Decimal("100"),
        taker_sell_volume=Decimal("80"),
    )

    assert snapshot.exchange == Exchange.BINANCE
    assert snapshot.oi_change_5m_pct == Decimal("1.5")
    assert snapshot.taker_buy_sell_ratio == Decimal("1.3")
