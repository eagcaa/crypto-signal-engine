import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
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
from crypto_signal_engine.features.technical import TechnicalFeatureSnapshot
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

        await aggregator.update_technical(
            TechnicalFeatureSnapshot(
                interval="5m",
                close=Decimal("85000"),
                ema_fast=Decimal("85100"),
                ema_slow=Decimal("84900"),
                ema_spread_atr=Decimal("0.4"),
                atr=Decimal("500"),
                atr_pct=Decimal("0.588"),
                trend_score=Decimal("0.4"),
                trend_regime="uptrend",
                volatility_regime="normal",
            )
        )

        snapshot = await aggregator.snapshot(_market_snapshot(now))

        assert snapshot.spot_cvd_1m == Decimal("2")
        assert snapshot.spot_cvd_5m == Decimal("1")
        assert snapshot.spot_cvd_15m == Decimal("1")
        assert snapshot.spot_cvd_ratio_1m == Decimal("1")
        assert snapshot.spot_cvd_ratio_5m == Decimal("1") / Decimal("3")
        assert snapshot.spot_cvd_ratio_15m == Decimal("1") / Decimal("3")
        assert snapshot.history_seconds == 180
        assert snapshot.trend_score_5m == Decimal("0.4")
        assert snapshot.trend_regime_5m == "uptrend"
        assert snapshot.atr_pct_5m == Decimal("0.588")
        assert snapshot.trend_score_15m is None
        assert snapshot.long_liquidations_5m_usd == Decimal("85000")
        assert snapshot.short_liquidations_5m_usd == Decimal("255000")
        assert snapshot.liquidation_imbalance_5m == Decimal("0.5")

    asyncio.run(run())



def test_future_derivatives_snapshot_is_not_used() -> None:
    async def run() -> None:
        now = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
        aggregator = ResearchFeatureAggregator("BTCUSDT")

        await aggregator.update_derivatives(
            ExchangeDerivativesSnapshot(
                exchange=Exchange.BINANCE,
                symbol="BTCUSDT",
                timestamp=now + timedelta(seconds=5),
                open_interest=Decimal("100"),
                open_interest_value_usd=Decimal("100000"),
                oi_change_5m_pct=Decimal("0.50"),
                oi_change_15m_pct=Decimal("0.75"),
                funding_rate=Decimal("0.0001"),
                long_short_ratio=Decimal("1.10"),
                long_account_ratio=Decimal("0.52"),
                short_account_ratio=Decimal("0.48"),
                top_trader_long_short_ratio=Decimal("1.20"),
                taker_buy_sell_ratio=Decimal("1.30"),
                taker_buy_volume=Decimal("1000"),
                taker_sell_volume=Decimal("900"),
            )
        )

        snapshot = await aggregator.snapshot(_market_snapshot(now))

        assert snapshot.binance_oi_change_5m_pct is None
        assert snapshot.binance_oi_change_15m_pct is None
        assert snapshot.binance_funding_rate is None
        assert snapshot.binance_long_short_ratio is None
        assert snapshot.binance_top_trader_long_short_ratio is None
        assert snapshot.binance_taker_buy_sell_ratio is None

    asyncio.run(run())
