from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import ResearchFeatureSnapshotRow
from crypto_signal_engine.features.research import ResearchFeatureSnapshot


class ResearchFeatureRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, snapshot: ResearchFeatureSnapshot) -> None:
        async with self._session_factory() as session:
            session.add(
                ResearchFeatureSnapshotRow(
                    timestamp=snapshot.timestamp,
                    symbol=snapshot.symbol,
                    price=snapshot.price,
                    spot_cvd_1m=snapshot.spot_cvd_1m,
                    spot_cvd_5m=snapshot.spot_cvd_5m,
                    spot_cvd_15m=snapshot.spot_cvd_15m,
                    futures_cvd_1m=snapshot.futures_cvd_1m,
                    futures_cvd_5m=snapshot.futures_cvd_5m,
                    futures_cvd_15m=snapshot.futures_cvd_15m,
                    spot_cvd_ratio_1m=snapshot.spot_cvd_ratio_1m,
                    spot_cvd_ratio_5m=snapshot.spot_cvd_ratio_5m,
                    spot_cvd_ratio_15m=snapshot.spot_cvd_ratio_15m,
                    futures_cvd_ratio_1m=snapshot.futures_cvd_ratio_1m,
                    futures_cvd_ratio_5m=snapshot.futures_cvd_ratio_5m,
                    futures_cvd_ratio_15m=snapshot.futures_cvd_ratio_15m,
                    spot_trade_sources=snapshot.spot_trade_sources,
                    futures_trade_sources=snapshot.futures_trade_sources,
                    history_seconds=snapshot.history_seconds,
                    binance_oi_change_5m_pct=snapshot.binance_oi_change_5m_pct,
                    binance_oi_change_15m_pct=snapshot.binance_oi_change_15m_pct,
                    bybit_oi_change_5m_pct=snapshot.bybit_oi_change_5m_pct,
                    bybit_oi_change_15m_pct=snapshot.bybit_oi_change_15m_pct,
                    binance_funding_rate=snapshot.binance_funding_rate,
                    bybit_funding_rate=snapshot.bybit_funding_rate,
                    binance_long_short_ratio=snapshot.binance_long_short_ratio,
                    bybit_long_short_ratio=snapshot.bybit_long_short_ratio,
                    binance_top_trader_long_short_ratio=(
                        snapshot.binance_top_trader_long_short_ratio
                    ),
                    binance_taker_buy_sell_ratio=(
                        snapshot.binance_taker_buy_sell_ratio
                    ),
                    long_liquidations_5m_usd=snapshot.long_liquidations_5m_usd,
                    short_liquidations_5m_usd=snapshot.short_liquidations_5m_usd,
                    liquidation_imbalance_5m=snapshot.liquidation_imbalance_5m,
                    long_liquidations_15m_usd=snapshot.long_liquidations_15m_usd,
                    short_liquidations_15m_usd=snapshot.short_liquidations_15m_usd,
                    liquidation_imbalance_15m=snapshot.liquidation_imbalance_15m,
                    binance_book_imbalance=snapshot.binance_book_imbalance,
                    bybit_book_imbalance=snapshot.bybit_book_imbalance,
                    market_data_quality=snapshot.market_data_quality,
                )
            )
            await session.commit()
