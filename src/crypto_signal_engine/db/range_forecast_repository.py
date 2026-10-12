from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import RangeForecastRow


@dataclass(frozen=True, slots=True)
class RangeForecastRecord:
    id: UUID
    request_id: UUID
    symbol: str
    generated_at: datetime
    anchor_at: datetime
    anchor_price: Decimal
    horizon_minutes: int
    target_coverage: Decimal
    lower_price: Decimal
    upper_price: Decimal
    calibration_count: int
    volatility_scaled: bool
    due_at: datetime
    status: str


class RangeForecastRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add_many(
        self,
        *,
        request_id: UUID,
        symbol: str,
        generated_at: datetime,
        forecasts: list[dict[str, object]],
    ) -> int:
        if not forecasts:
            return 0

        async with self._session_factory() as session:
            for item in forecasts:
                session.add(
                    RangeForecastRow(
                        id=uuid4(),
                        request_id=request_id,
                        symbol=symbol.upper(),
                        generated_at=generated_at,
                        anchor_at=item["anchor_at"],
                        anchor_price=Decimal(str(item["anchor_price"])),
                        horizon_minutes=int(item["horizon_minutes"]),
                        target_coverage=Decimal(str(item["target_coverage"])),
                        lower_price=Decimal(str(item["lower_price"])),
                        upper_price=Decimal(str(item["upper_price"])),
                        calibration_count=int(item["calibration_count"]),
                        volatility_scaled=bool(item["volatility_scaled"]),
                        due_at=item["due_at"],
                        status="pending",
                    )
                )
            await session.commit()
        return len(forecasts)

    async def due_pending(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[RangeForecastRecord]:
        async with self._session_factory() as session:
            query = (
                select(RangeForecastRow)
                .where(
                    RangeForecastRow.status == "pending",
                    RangeForecastRow.due_at <= now,
                )
                .order_by(RangeForecastRow.due_at.asc())
                .limit(limit)
            )
            rows = list((await session.scalars(query)).all())
            return [
                RangeForecastRecord(
                    id=row.id,
                    request_id=row.request_id,
                    symbol=row.symbol,
                    generated_at=row.generated_at,
                    anchor_at=row.anchor_at,
                    anchor_price=row.anchor_price,
                    horizon_minutes=row.horizon_minutes,
                    target_coverage=row.target_coverage,
                    lower_price=row.lower_price,
                    upper_price=row.upper_price,
                    calibration_count=row.calibration_count,
                    volatility_scaled=row.volatility_scaled,
                    due_at=row.due_at,
                    status=row.status,
                )
                for row in rows
            ]

    async def mark_evaluated(
        self,
        forecast_id: UUID,
        *,
        observed_low: Decimal,
        observed_high: Decimal,
        covered: bool,
        evaluated_at: datetime,
        source: str = "binance_spot_klines",
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(RangeForecastRow, forecast_id)
            if row is None or row.status != "pending":
                return
            row.observed_low = observed_low
            row.observed_high = observed_high
            row.covered = covered
            row.evaluated_at = evaluated_at
            row.evaluation_source = source
            row.status = "evaluated"
            await session.commit()

    async def mark_no_data(
        self,
        forecast_id: UUID,
        *,
        evaluated_at: datetime,
        source: str = "binance_spot_klines",
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(RangeForecastRow, forecast_id)
            if row is None or row.status != "pending":
                return
            row.evaluated_at = evaluated_at
            row.evaluation_source = source
            row.status = "no_data"
            await session.commit()
