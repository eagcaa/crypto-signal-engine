from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import (
    ExchangeDerivativesSnapshotRow,
    LiquidationEventRow,
)
from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
    LiquidationEvent,
)


class DerivativesRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add_snapshot(
        self,
        snapshot: ExchangeDerivativesSnapshot,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                ExchangeDerivativesSnapshotRow(
                    timestamp=snapshot.timestamp,
                    exchange=snapshot.exchange.value,
                    symbol=snapshot.symbol,
                    open_interest=snapshot.open_interest,
                    open_interest_value_usd=snapshot.open_interest_value_usd,
                    oi_change_5m_pct=snapshot.oi_change_5m_pct,
                    oi_change_15m_pct=snapshot.oi_change_15m_pct,
                    funding_rate=snapshot.funding_rate,
                    long_short_ratio=snapshot.long_short_ratio,
                    long_account_ratio=snapshot.long_account_ratio,
                    short_account_ratio=snapshot.short_account_ratio,
                    top_trader_long_short_ratio=(
                        snapshot.top_trader_long_short_ratio
                    ),
                    taker_buy_sell_ratio=snapshot.taker_buy_sell_ratio,
                    taker_buy_volume=snapshot.taker_buy_volume,
                    taker_sell_volume=snapshot.taker_sell_volume,
                )
            )
            await session.commit()

    async def add_liquidation(
        self,
        event: LiquidationEvent,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                LiquidationEventRow(
                    exchange=event.exchange.value,
                    symbol=event.symbol,
                    event_time=event.event_time,
                    position_side=event.position_side.value,
                    price=event.price,
                    quantity=event.quantity,
                    notional_usd=event.notional_usd,
                )
            )
            await session.commit()
