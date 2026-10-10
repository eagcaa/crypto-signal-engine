from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import ResearchFeatureSnapshotRow
from crypto_signal_engine.features.research import ResearchFeatureSnapshot


@dataclass(frozen=True, slots=True)
class ResearchFeatureCoverage:
    symbol: str
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    rows: int
    usable_rows: int

    @property
    def span_hours(self) -> float:
        if self.first_timestamp is None or self.last_timestamp is None:
            return 0.0
        return (
            self.last_timestamp - self.first_timestamp
        ).total_seconds() / 3600.0


class ResearchFeatureRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _to_row(snapshot: ResearchFeatureSnapshot) -> ResearchFeatureSnapshotRow:
        return ResearchFeatureSnapshotRow(
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
            trend_score_5m=snapshot.trend_score_5m,
            trend_score_15m=snapshot.trend_score_15m,
            trend_score_1h=snapshot.trend_score_1h,
            trend_score_4h=snapshot.trend_score_4h,
            atr_pct_5m=snapshot.atr_pct_5m,
            atr_pct_15m=snapshot.atr_pct_15m,
            atr_pct_1h=snapshot.atr_pct_1h,
            atr_pct_4h=snapshot.atr_pct_4h,
            trend_regime_5m=snapshot.trend_regime_5m,
            trend_regime_15m=snapshot.trend_regime_15m,
            trend_regime_1h=snapshot.trend_regime_1h,
            trend_regime_4h=snapshot.trend_regime_4h,
            volatility_regime_5m=snapshot.volatility_regime_5m,
            volatility_regime_15m=snapshot.volatility_regime_15m,
            volatility_regime_1h=snapshot.volatility_regime_1h,
            volatility_regime_4h=snapshot.volatility_regime_4h,
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
            binance_taker_buy_sell_ratio=snapshot.binance_taker_buy_sell_ratio,
            long_liquidations_5m_usd=snapshot.long_liquidations_5m_usd,
            short_liquidations_5m_usd=snapshot.short_liquidations_5m_usd,
            liquidation_imbalance_5m=snapshot.liquidation_imbalance_5m,
            long_liquidations_15m_usd=snapshot.long_liquidations_15m_usd,
            short_liquidations_15m_usd=snapshot.short_liquidations_15m_usd,
            liquidation_imbalance_15m=snapshot.liquidation_imbalance_15m,
            binance_book_imbalance=snapshot.binance_book_imbalance,
            bybit_book_imbalance=snapshot.bybit_book_imbalance,
            market_data_quality=snapshot.market_data_quality,
            liquidation_data_available=snapshot.liquidation_data_available,
            dataset_provenance=snapshot.dataset_provenance,
        )

    async def add(self, snapshot: ResearchFeatureSnapshot) -> None:
        async with self._session_factory() as session:
            session.add(self._to_row(snapshot))
            await session.commit()

    async def add_many(
        self,
        snapshots: list[ResearchFeatureSnapshot],
        *,
        batch_size: int = 1000,
    ) -> tuple[int, int]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        inserted = 0
        skipped = 0

        for offset in range(0, len(snapshots), batch_size):
            batch = snapshots[offset : offset + batch_size]
            if not batch:
                continue

            symbol = batch[0].symbol.upper()
            if any(item.symbol.upper() != symbol for item in batch):
                raise ValueError("A batch cannot mix symbols")

            timestamps = [item.timestamp for item in batch]
            async with self._session_factory() as session:
                existing_rows = list(
                    (
                        await session.scalars(
                            select(ResearchFeatureSnapshotRow).where(
                                ResearchFeatureSnapshotRow.symbol == symbol,
                                ResearchFeatureSnapshotRow.timestamp.in_(timestamps),
                            )
                        )
                    ).all()
                )
                existing_keys = {
                    (row.timestamp, row.dataset_provenance)
                    for row in existing_rows
                }

                rows_to_add: list[ResearchFeatureSnapshotRow] = []
                for snapshot in batch:
                    key = (
                        snapshot.timestamp,
                        snapshot.dataset_provenance,
                    )
                    if key not in existing_keys:
                        rows_to_add.append(self._to_row(snapshot))
                        existing_keys.add(key)
                        continue
                    skipped += 1

                session.add_all(rows_to_add)
                await session.commit()
                inserted += len(rows_to_add)

        return inserted, skipped


    async def replace_many(
        self,
        snapshots: list[ResearchFeatureSnapshot],
        *,
        batch_size: int = 1000,
    ) -> tuple[int, int]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        written = 0
        replaced = 0

        for offset in range(0, len(snapshots), batch_size):
            batch = snapshots[offset : offset + batch_size]
            if not batch:
                continue

            symbol = batch[0].symbol.upper()
            if any(item.symbol.upper() != symbol for item in batch):
                raise ValueError("A batch cannot mix symbols")

            timestamps = [item.timestamp for item in batch]
            async with self._session_factory() as session:
                existing_rows = list(
                    (
                        await session.scalars(
                            select(ResearchFeatureSnapshotRow).where(
                                ResearchFeatureSnapshotRow.symbol == symbol,
                                ResearchFeatureSnapshotRow.timestamp.in_(timestamps),
                            )
                        )
                    ).all()
                )
                existing_by_key = {
                    (row.timestamp, row.dataset_provenance): row
                    for row in existing_rows
                }

                for snapshot in batch:
                    key = (
                        snapshot.timestamp,
                        snapshot.dataset_provenance,
                    )
                    existing = existing_by_key.get(key)
                    if existing is not None:
                        await session.delete(existing)
                        replaced += 1

                    session.add(self._to_row(snapshot))
                    written += 1

                await session.commit()

        return written, replaced


    async def coverage(
        self,
        symbol: str,
    ) -> ResearchFeatureCoverage:
        symbol = symbol.upper()
        async with self._session_factory() as session:
            summary = (
                await session.execute(
                    select(
                        func.min(ResearchFeatureSnapshotRow.timestamp),
                        func.max(ResearchFeatureSnapshotRow.timestamp),
                        func.count(),
                    ).where(
                        ResearchFeatureSnapshotRow.symbol == symbol
                    )
                )
            ).one()

            usable_rows = (
                await session.scalar(
                    select(func.count()).where(
                        ResearchFeatureSnapshotRow.symbol == symbol,
                        ResearchFeatureSnapshotRow.price.is_not(None),
                        ResearchFeatureSnapshotRow.history_seconds >= 900,
                        ResearchFeatureSnapshotRow.trend_regime_15m.is_not(None),
                        ResearchFeatureSnapshotRow.volatility_regime_15m.is_not(None),
                    )
                )
            ) or 0

            return ResearchFeatureCoverage(
                symbol=symbol,
                first_timestamp=summary[0],
                last_timestamp=summary[1],
                rows=int(summary[2] or 0),
                usable_rows=int(usable_rows),
            )
