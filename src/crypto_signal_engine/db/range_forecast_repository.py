from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import sqrt
from uuid import UUID, uuid4

from sqlalchemy import func, select
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


@dataclass(frozen=True, slots=True)
class RangeCoverageStat:
    symbol: str
    horizon_minutes: int
    evaluated: int
    covered: int
    coverage_pct: Decimal
    pending: int
    no_data: int
    # Forecasts for the same coin/horizon asked shortly after each other share
    # most of their future path, so they are not independent tests. Counting
    # only one forecast per horizon-length window gives honest statistics.
    independent: int = 0
    independent_covered: int = 0

    @property
    def independent_coverage_pct(self) -> float | None:
        if not self.independent:
            return None
        return self.independent_covered * 100.0 / self.independent

    @property
    def margin_pct(self) -> float | None:
        """Approximate 95% margin of error of the independent coverage."""
        if not self.independent:
            return None
        share = self.independent_covered / self.independent
        return 196.0 * sqrt(max(share * (1.0 - share), 0.0) / self.independent)


def count_independent(
    outcomes: list[tuple[datetime, bool]],
    horizon_minutes: int,
) -> tuple[int, int]:
    """Keep a forecast only if it starts after the previous kept one ended."""
    kept = 0
    kept_covered = 0
    next_allowed: datetime | None = None
    window = timedelta(minutes=horizon_minutes)
    for generated_at, covered in sorted(outcomes, key=lambda item: item[0]):
        if next_allowed is not None and generated_at < next_allowed:
            continue
        kept += 1
        kept_covered += int(bool(covered))
        next_allowed = generated_at + window
    return kept, kept_covered


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


    async def coverage_stats(
        self,
        *,
        symbol: str | None = None,
    ) -> list[RangeCoverageStat]:
        async with self._session_factory() as session:
            normalized = symbol.upper() if symbol else None

            outcome_query = select(
                RangeForecastRow.symbol,
                RangeForecastRow.horizon_minutes,
                RangeForecastRow.generated_at,
                RangeForecastRow.covered,
            ).where(RangeForecastRow.status == "evaluated")
            if normalized:
                outcome_query = outcome_query.where(
                    RangeForecastRow.symbol == normalized
                )
            outcomes: dict[tuple[str, int], list[tuple[datetime, bool]]] = {}
            for row_symbol, horizon, generated_at, covered in (
                await session.execute(outcome_query)
            ).all():
                outcomes.setdefault((row_symbol, int(horizon)), []).append(
                    (generated_at, bool(covered))
                )
            evaluated_rows = [
                (
                    key[0],
                    key[1],
                    len(items),
                    sum(int(covered) for _, covered in items),
                )
                for key, items in outcomes.items()
            ]

            status_query = (
                select(
                    RangeForecastRow.symbol,
                    RangeForecastRow.horizon_minutes,
                    RangeForecastRow.status,
                    func.count(RangeForecastRow.id),
                )
                .where(RangeForecastRow.status.in_(("pending", "no_data")))
                .group_by(
                    RangeForecastRow.symbol,
                    RangeForecastRow.horizon_minutes,
                    RangeForecastRow.status,
                )
            )
            if normalized:
                status_query = status_query.where(
                    RangeForecastRow.symbol == normalized
                )
            status_rows = (await session.execute(status_query)).all()

            statuses: dict[tuple[str, int], dict[str, int]] = {}
            for row_symbol, horizon, status, count in status_rows:
                statuses.setdefault((row_symbol, int(horizon)), {})[status] = int(
                    count or 0
                )

            results: list[RangeCoverageStat] = []
            seen: set[tuple[str, int]] = set()

            for row_symbol, horizon, total, covered in evaluated_rows:
                key = (row_symbol, int(horizon))
                seen.add(key)
                evaluated = int(total or 0)
                covered_count = int(covered or 0)
                status_counts = statuses.get(key, {})
                coverage = (
                    Decimal(covered_count) * Decimal("100") / Decimal(evaluated)
                    if evaluated
                    else Decimal("0")
                )
                independent, independent_covered = count_independent(
                    outcomes[key],
                    int(horizon),
                )
                results.append(
                    RangeCoverageStat(
                        symbol=row_symbol,
                        horizon_minutes=int(horizon),
                        evaluated=evaluated,
                        covered=covered_count,
                        coverage_pct=coverage,
                        pending=status_counts.get("pending", 0),
                        no_data=status_counts.get("no_data", 0),
                        independent=independent,
                        independent_covered=independent_covered,
                    )
                )

            for key, status_counts in statuses.items():
                if key in seen:
                    continue
                row_symbol, horizon = key
                results.append(
                    RangeCoverageStat(
                        symbol=row_symbol,
                        horizon_minutes=horizon,
                        evaluated=0,
                        covered=0,
                        coverage_pct=Decimal("0"),
                        pending=status_counts.get("pending", 0),
                        no_data=status_counts.get("no_data", 0),
                    )
                )

            return sorted(
                results,
                key=lambda item: (item.symbol, item.horizon_minutes),
            )
