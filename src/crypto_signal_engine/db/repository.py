from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import MarketSnapshotRow
from crypto_signal_engine.market import MarketSnapshot


class MarketSnapshotRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, snapshot: MarketSnapshot) -> None:
        row = self.to_row(snapshot)

        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

    @staticmethod
    def to_row(snapshot: MarketSnapshot) -> MarketSnapshotRow:
        return MarketSnapshotRow(
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            price=snapshot.price,
            binance_spot_cvd=snapshot.binance_spot_cvd,
            binance_futures_cvd=snapshot.binance_futures_cvd,
            bybit_spot_cvd=snapshot.bybit_spot_cvd,
            bybit_futures_cvd=snapshot.bybit_futures_cvd,
            spot_cvd_total=snapshot.spot_cvd_total,
            futures_cvd_total=snapshot.futures_cvd_total,
            spot_futures_divergence=snapshot.spot_futures_divergence,
            binance_book_imbalance=snapshot.binance_book_imbalance,
            bybit_book_imbalance=snapshot.bybit_book_imbalance,
            cross_exchange_book_divergence=(
                snapshot.cross_exchange_book_divergence
            ),
            buy_pressure=snapshot.buy_pressure,
            sell_pressure=snapshot.sell_pressure,
            binance_spot_age_ms=snapshot.binance_spot_age_ms,
            binance_futures_age_ms=snapshot.binance_futures_age_ms,
            bybit_spot_age_ms=snapshot.bybit_spot_age_ms,
            bybit_futures_age_ms=snapshot.bybit_futures_age_ms,
            binance_book_age_ms=snapshot.binance_book_age_ms,
            bybit_book_age_ms=snapshot.bybit_book_age_ms,
            data_quality=snapshot.data_quality,
        )
