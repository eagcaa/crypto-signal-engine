import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.derivatives import (
    LiquidatedPositionSide,
    LiquidationEvent,
)
from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    TradeSide,
    TradeTick,
)
from crypto_signal_engine.features.research import ResearchFeatureAggregator
from crypto_signal_engine.market import MarketSnapshot


def _market_snapshot(now: datetime) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=now,
        price=Decimal("85000"),
        binance_spot_cvd=Decimal("0"),
        binance_futures_cvd=None,
        bybit_spot_cvd=Decimal("0"),
        bybit_futures_cvd=Decimal("0"),
        binance_book_imbalance=Decimal("0.2"),
        bybit_book_imbalance=Decimal("-0.1"),
        spot_cvd_total=Decimal("0"),
        futures_cvd_total=None,
        spot_futures_divergence=None,
        cross_exchange_book_divergence=Decimal("0.3"),
        buy_pressure=Decimal("0.05"),
        sell_pressure=Decimal("0"),
        binance_spot_age_ms=10,
        binance_futures_age_ms=None,
        bybit_spot_age_ms=10,
        bybit_futures_age_ms=10,
        binance_book_age_ms=10,
        bybit_book_age_ms=10,
        data_quality=Decimal("0.83"),
    )


def test_rolling_cvd_and_liquidation_imbalance() -> None:
    async def run() -> None:
        now = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
        aggregator = ResearchFeatureAggregator("BTCUSDT")

        await aggregator.update_trade(
            TradeTick(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                symbol="BTCUSDT",
                event_time=now - timedelta(seconds=20),
                price=Decimal("85000"),
                quantity=Decimal("2"),
                side=TradeSide.BUY,
            )
        )
        await aggregator.update_trade(
            TradeTick(
                exchange=Exchange.BYBIT,
                market_type=MarketType.SPOT,
                symbol="BTCUSDT",
                event_time=now - timedelta(minutes=3),
                price=Decimal("85000"),
                quantity=Decimal("1"),
                side=TradeSide.SELL,
            )
        )

        await aggregator.update_liquidation(
            LiquidationEvent(
                exchange=Exchange.BYBIT,
                symbol="BTCUSDT",
                event_time=now - timedelta(minutes=1),
                position_side=LiquidatedPositionSide.LONG,
                price=Decimal("85000"),
                quantity=Decimal("1"),
            )
        )
        await aggregator.update_liquidation(
            LiquidationEvent(
                exchange=Exchange.BINANCE,
                symbol="BTCUSDT",
                event_time=now - timedelta(minutes=2),
                position_side=LiquidatedPositionSide.SHORT,
                price=Decimal("85000"),
                quantity=Decimal("3"),
            )
        )

        snapshot = await aggregator.snapshot(_market_snapshot(now))

        assert snapshot.spot_cvd_1m == Decimal("2")
        assert snapshot.spot_cvd_5m == Decimal("1")
        assert snapshot.spot_cvd_15m == Decimal("1")
        assert snapshot.history_seconds == 180
        assert snapshot.long_liquidations_5m_usd == Decimal("85000")
        assert snapshot.short_liquidations_5m_usd == Decimal("255000")
        assert snapshot.liquidation_imbalance_5m == Decimal("0.5")

    asyncio.run(run())
