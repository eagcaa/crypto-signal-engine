from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import MarketSnapshotRow, ResearchFeatureSnapshotRow
from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.replay.models import ReplayPricePoint


class ReplayDataRepository:
    """Read persisted research features and canonical price samples for replay."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def load_features(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[ResearchFeatureSnapshot]:
        async with self._session_factory() as session:
            query = (
                select(ResearchFeatureSnapshotRow)
                .where(
                    ResearchFeatureSnapshotRow.symbol == symbol.upper(),
                    ResearchFeatureSnapshotRow.timestamp >= start,
                    ResearchFeatureSnapshotRow.timestamp <= end,
                )
                .order_by(ResearchFeatureSnapshotRow.timestamp.asc())
            )
            rows = list((await session.scalars(query)).all())
            return [self._to_feature_snapshot(row) for row in rows]

    async def load_price_points(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[ReplayPricePoint]:
        async with self._session_factory() as session:
            query = (
                select(MarketSnapshotRow)
                .where(
                    MarketSnapshotRow.symbol == symbol.upper(),
                    MarketSnapshotRow.timestamp >= start,
                    MarketSnapshotRow.timestamp <= end,
                    MarketSnapshotRow.price.is_not(None),
                )
                .order_by(MarketSnapshotRow.timestamp.asc())
            )
            rows = list((await session.scalars(query)).all())

            return [
                ReplayPricePoint(
                    symbol=row.symbol,
                    timestamp=row.timestamp,
                    price=row.price,
                )
                for row in rows
                if row.price is not None
            ]

    @staticmethod
    def _to_feature_snapshot(
        row: ResearchFeatureSnapshotRow,
    ) -> ResearchFeatureSnapshot:
        return ResearchFeatureSnapshot(
            symbol=row.symbol,
            timestamp=row.timestamp,
            price=row.price,
            spot_cvd_1m=row.spot_cvd_1m,
            spot_cvd_5m=row.spot_cvd_5m,
            spot_cvd_15m=row.spot_cvd_15m,
            futures_cvd_1m=row.futures_cvd_1m,
            futures_cvd_5m=row.futures_cvd_5m,
            futures_cvd_15m=row.futures_cvd_15m,
            spot_cvd_ratio_1m=row.spot_cvd_ratio_1m,
            spot_cvd_ratio_5m=row.spot_cvd_ratio_5m,
            spot_cvd_ratio_15m=row.spot_cvd_ratio_15m,
            futures_cvd_ratio_1m=row.futures_cvd_ratio_1m,
            futures_cvd_ratio_5m=row.futures_cvd_ratio_5m,
            futures_cvd_ratio_15m=row.futures_cvd_ratio_15m,
            spot_trade_sources=row.spot_trade_sources,
            futures_trade_sources=row.futures_trade_sources,
            history_seconds=row.history_seconds,
            trend_score_5m=row.trend_score_5m,
            trend_score_15m=row.trend_score_15m,
            trend_score_1h=row.trend_score_1h,
            trend_score_4h=row.trend_score_4h,
            atr_pct_5m=row.atr_pct_5m,
            atr_pct_15m=row.atr_pct_15m,
            atr_pct_1h=row.atr_pct_1h,
            atr_pct_4h=row.atr_pct_4h,
            trend_regime_5m=row.trend_regime_5m,
            trend_regime_15m=row.trend_regime_15m,
            trend_regime_1h=row.trend_regime_1h,
            trend_regime_4h=row.trend_regime_4h,
            volatility_regime_5m=row.volatility_regime_5m,
            volatility_regime_15m=row.volatility_regime_15m,
            volatility_regime_1h=row.volatility_regime_1h,
            volatility_regime_4h=row.volatility_regime_4h,
            binance_oi_change_5m_pct=row.binance_oi_change_5m_pct,
            binance_oi_change_15m_pct=row.binance_oi_change_15m_pct,
            bybit_oi_change_5m_pct=row.bybit_oi_change_5m_pct,
            bybit_oi_change_15m_pct=row.bybit_oi_change_15m_pct,
            binance_funding_rate=row.binance_funding_rate,
            bybit_funding_rate=row.bybit_funding_rate,
            binance_long_short_ratio=row.binance_long_short_ratio,
            bybit_long_short_ratio=row.bybit_long_short_ratio,
            binance_top_trader_long_short_ratio=(
                row.binance_top_trader_long_short_ratio
            ),
            binance_taker_buy_sell_ratio=row.binance_taker_buy_sell_ratio,
            long_liquidations_5m_usd=row.long_liquidations_5m_usd,
            short_liquidations_5m_usd=row.short_liquidations_5m_usd,
            liquidation_imbalance_5m=row.liquidation_imbalance_5m,
            long_liquidations_15m_usd=row.long_liquidations_15m_usd,
            short_liquidations_15m_usd=row.short_liquidations_15m_usd,
            liquidation_imbalance_15m=row.liquidation_imbalance_15m,
            binance_book_imbalance=row.binance_book_imbalance,
            bybit_book_imbalance=row.bybit_book_imbalance,
            market_data_quality=row.market_data_quality,
        )
