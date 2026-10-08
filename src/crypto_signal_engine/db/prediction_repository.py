from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import (
    MarketSnapshotRow,
    PredictionEvaluationRow,
    PredictionRow,
)
from crypto_signal_engine.predictions import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationStatus,
)


class PredictionRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, prediction: Prediction) -> None:
        async with self._session_factory() as session:
            session.add(
                PredictionRow(
                    id=prediction.id,
                    symbol=prediction.symbol,
                    created_at=prediction.created_at,
                    expires_at=prediction.expires_at,
                    horizon_seconds=prediction.horizon_seconds,
                    direction=prediction.direction.value,
                    entry_price=prediction.entry_price,
                    raw_score=prediction.raw_score,
                    data_quality=prediction.data_quality,
                    model_name=prediction.model_name,
                )
            )
            await session.commit()

    async def evaluate_due(
        self,
        now: datetime,
        *,
        max_snapshot_delay_seconds: int = 30,
    ) -> list[PredictionEvaluation]:
        async with self._session_factory() as session:
            query = (
                select(PredictionRow)
                .outerjoin(
                    PredictionEvaluationRow,
                    PredictionEvaluationRow.prediction_id == PredictionRow.id,
                )
                .where(
                    PredictionRow.expires_at <= now,
                    PredictionEvaluationRow.prediction_id.is_(None),
                )
            )
            predictions = list((await session.scalars(query)).all())
            evaluations: list[PredictionEvaluation] = []

            for prediction in predictions:
                latest_allowed = prediction.expires_at + timedelta(
                    seconds=max_snapshot_delay_seconds
                )

                price_query = (
                    select(MarketSnapshotRow)
                    .where(
                        MarketSnapshotRow.symbol == prediction.symbol,
                        MarketSnapshotRow.timestamp >= prediction.expires_at,
                        MarketSnapshotRow.timestamp <= latest_allowed,
                        MarketSnapshotRow.price.is_not(None),
                    )
                    .order_by(MarketSnapshotRow.timestamp.asc())
                    .limit(1)
                )
                snapshot = await session.scalar(price_query)

                if snapshot is not None and snapshot.price is not None:
                    evaluation = self._build_evaluation(
                        prediction=prediction,
                        evaluated_at=snapshot.timestamp,
                        exit_price=snapshot.price,
                    )
                elif now > latest_allowed:
                    evaluation = PredictionEvaluation(
                        prediction_id=prediction.id,
                        status=PredictionEvaluationStatus.EXPIRED_WITHOUT_DATA,
                        evaluated_at=now,
                        exit_price=None,
                        return_pct=None,
                        success=None,
                    )
                else:
                    continue

                session.add(
                    PredictionEvaluationRow(
                        prediction_id=evaluation.prediction_id,
                        status=evaluation.status.value,
                        evaluated_at=evaluation.evaluated_at,
                        exit_price=evaluation.exit_price,
                        return_pct=evaluation.return_pct,
                        success=evaluation.success,
                    )
                )
                evaluations.append(evaluation)

            await session.commit()
            return evaluations

    @staticmethod
    def _build_evaluation(
        *,
        prediction: PredictionRow,
        evaluated_at: datetime,
        exit_price: Decimal,
    ) -> PredictionEvaluation:
        return_pct = (
            (exit_price - prediction.entry_price)
            / prediction.entry_price
        ) * Decimal("100")

        direction = PredictionDirection(prediction.direction)
        success = (
            return_pct > 0
            if direction == PredictionDirection.LONG
            else return_pct < 0
        )

        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            evaluated_at=evaluated_at,
            exit_price=exit_price,
            return_pct=return_pct,
            success=success,
        )

    async def latest_open_for_horizon(
        self,
        *,
        symbol: str,
        horizon_seconds: int,
    ) -> UUID | None:
        async with self._session_factory() as session:
            query = (
                select(PredictionRow.id)
                .outerjoin(
                    PredictionEvaluationRow,
                    PredictionEvaluationRow.prediction_id == PredictionRow.id,
                )
                .where(
                    PredictionRow.symbol == symbol,
                    PredictionRow.horizon_seconds == horizon_seconds,
                    PredictionEvaluationRow.prediction_id.is_(None),
                )
                .order_by(PredictionRow.created_at.desc())
                .limit(1)
            )
            return await session.scalar(query)
