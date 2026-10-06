from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import CoinGlassSnapshotRow
from crypto_signal_engine.integrations.coinglass import CoinGlassMarketSnapshot


class CoinGlassSnapshotRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, snapshot: CoinGlassMarketSnapshot) -> None:
        async with self._session_factory() as session:
            session.add(
                CoinGlassSnapshotRow(
                    timestamp=snapshot.timestamp,
                    symbol=snapshot.symbol,
                    open_interest_usd=snapshot.open_interest_usd,
                    oi_change_5m_pct=snapshot.oi_change_5m_pct,
                    oi_change_15m_pct=snapshot.oi_change_15m_pct,
                    funding_rate_binance=snapshot.funding_rate_binance,
                    funding_rate_bybit=snapshot.funding_rate_bybit,
                    taker_buy_ratio=snapshot.taker_buy_ratio,
                    taker_sell_ratio=snapshot.taker_sell_ratio,
                    taker_buy_volume_usd=snapshot.taker_buy_volume_usd,
                    taker_sell_volume_usd=snapshot.taker_sell_volume_usd,
                )
            )
            await session.commit()
