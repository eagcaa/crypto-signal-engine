import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Integer, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)


@dataclass(frozen=True, slots=True)
class EvaluationProvenanceStats:
    evaluation_source: str
    evaluation_version: str
    total: int
    take_profit: int
    stop_loss: int
    expired_no_touch: int
    expired_without_data: int
    unresolved: int


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
                    take_profit_pct=prediction.take_profit_pct,
                    stop_loss_pct=prediction.stop_loss_pct,
                    model_name=prediction.model_name,
                    feature_contributions_json=(
                        json.dumps(
                            {
                                key: str(value)
                                for key, value in (
                                    prediction.feature_contributions or {}
                                ).items()
                            },
                            sort_keys=True,
                        )
                        if prediction.feature_contributions
                        else None
                    ),
                    reason=prediction.reason,
                )
            )
            await session.commit()

    async def load_open_predictions(
        self,
        *,
        now: datetime,
        symbols: list[str] | None = None,
    ) -> list[Prediction]:
        """Load still-active predictions that have no persisted evaluation.

        Live tick evaluation keeps open predictions in RAM, so this method
        restores that state after a process restart.
        """
        async with self._session_factory() as session:
            query = (
                select(PredictionRow)
                .outerjoin(
                    PredictionEvaluationRow,
                    PredictionEvaluationRow.prediction_id == PredictionRow.id,
                )
                .where(
                    PredictionRow.expires_at >= now,
                    PredictionEvaluationRow.prediction_id.is_(None),
                )
                .order_by(PredictionRow.created_at.asc())
            )

            if symbols:
                normalized_symbols = [symbol.upper() for symbol in symbols]
                query = query.where(PredictionRow.symbol.in_(normalized_symbols))

            rows = list((await session.scalars(query)).all())

            predictions: list[Prediction] = []
            for row in rows:
                contributions = None
                if row.feature_contributions_json:
                    parsed = json.loads(row.feature_contributions_json)
                    contributions = {
                        key: Decimal(value)
                        for key, value in parsed.items()
                    }

                predictions.append(
                    Prediction(
                        id=row.id,
                        symbol=row.symbol,
                        created_at=row.created_at,
                        expires_at=row.expires_at,
                        horizon_seconds=row.horizon_seconds,
                        direction=PredictionDirection(row.direction),
                        entry_price=row.entry_price,
                        raw_score=row.raw_score,
                        data_quality=row.data_quality,
                        take_profit_pct=row.take_profit_pct,
                        stop_loss_pct=row.stop_loss_pct,
                        model_name=row.model_name,
                        feature_contributions=contributions,
                        reason=row.reason,
                    )
                )

            return predictions

    async def add_evaluation(
        self,
        evaluation: PredictionEvaluation,
    ) -> bool:
        """Persist an evaluation once.

        Live tick evaluation and snapshot fallback may race. PostgreSQL
        ON CONFLICT keeps the first result and prevents duplicate rows.
        """
        async with self._session_factory() as session:
            statement = (
                pg_insert(PredictionEvaluationRow)
                .values(
                    prediction_id=evaluation.prediction_id,
                    status=evaluation.status.value,
                    outcome=evaluation.outcome.value,
                    label=evaluation.label,
                    evaluated_at=evaluation.evaluated_at,
                    exit_price=evaluation.exit_price,
                    return_pct=evaluation.return_pct,
                    success=evaluation.success,
                    evaluation_source=evaluation.evaluation_source,
                    evaluation_version=evaluation.evaluation_version,
                )
                .on_conflict_do_nothing(
                    index_elements=[PredictionEvaluationRow.prediction_id]
                )
                .returning(PredictionEvaluationRow.prediction_id)
            )
            inserted_id = await session.scalar(statement)
            await session.commit()
            return inserted_id is not None

    async def evaluate_due(
        self,
        now: datetime,
        *,
        max_snapshot_delay_seconds: int = 30,
    ) -> list[PredictionEvaluation]:
        """Evaluate open predictions using first-touch TP/SL semantics.

        TP is +0.60% and SL is -0.30% relative to the prediction direction.
        A prediction is evaluated immediately when either barrier is first
        observed in persisted market snapshots. If neither barrier is touched
        by expiry, it receives label 0 (expired_no_touch).

        Since market snapshots are sampled periodically, this is an
        approximation of true tick-level first touch.
        """

        async with self._session_factory() as session:
            query = (
                select(PredictionRow)
                .outerjoin(
                    PredictionEvaluationRow,
                    PredictionEvaluationRow.prediction_id == PredictionRow.id,
                )
                .where(
                    PredictionRow.created_at < now,
                    PredictionEvaluationRow.prediction_id.is_(None),
                )
            )
            predictions = list((await session.scalars(query)).all())
            evaluations: list[PredictionEvaluation] = []

            for prediction in predictions:
                observation_end = min(now, prediction.expires_at)

                path_query = (
                    select(MarketSnapshotRow)
                    .where(
                        MarketSnapshotRow.symbol == prediction.symbol,
                        MarketSnapshotRow.timestamp > prediction.created_at,
                        MarketSnapshotRow.timestamp <= observation_end,
                        MarketSnapshotRow.price.is_not(None),
                    )
                    .order_by(MarketSnapshotRow.timestamp.asc())
                )
                snapshots = list((await session.scalars(path_query)).all())

                evaluation = self._first_touch_evaluation(
                    prediction=prediction,
                    snapshots=snapshots,
                )

                if evaluation is None and now >= prediction.expires_at:
                    latest_allowed = prediction.expires_at + timedelta(
                        seconds=max_snapshot_delay_seconds
                    )

                    expiry_query = (
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
                    expiry_snapshot = await session.scalar(expiry_query)

                    if (
                        expiry_snapshot is not None
                        and expiry_snapshot.price is not None
                    ):
                        evaluation = self._expired_no_touch_evaluation(
                            prediction=prediction,
                            evaluated_at=expiry_snapshot.timestamp,
                            exit_price=expiry_snapshot.price,
                        )
                    elif now > latest_allowed:
                        evaluation = PredictionEvaluation(
                            prediction_id=prediction.id,
                            status=(
                                PredictionEvaluationStatus.EXPIRED_WITHOUT_DATA
                            ),
                            outcome=(
                                PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA
                            ),
                            label=None,
                            evaluated_at=now,
                            exit_price=None,
                            return_pct=None,
                            success=None,
                            evaluation_source="persisted_snapshot",
                            evaluation_version="first_touch_v1",
                        )

                if evaluation is None:
                    continue

                statement = (
                    pg_insert(PredictionEvaluationRow)
                    .values(
                        prediction_id=evaluation.prediction_id,
                        status=evaluation.status.value,
                        outcome=evaluation.outcome.value,
                        label=evaluation.label,
                        evaluated_at=evaluation.evaluated_at,
                        exit_price=evaluation.exit_price,
                        return_pct=evaluation.return_pct,
                        success=evaluation.success,
                        evaluation_source=evaluation.evaluation_source,
                        evaluation_version=evaluation.evaluation_version,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[PredictionEvaluationRow.prediction_id]
                    )
                    .returning(PredictionEvaluationRow.prediction_id)
                )
                inserted_id = await session.scalar(statement)
                if inserted_id is not None:
                    evaluations.append(evaluation)

            await session.commit()
            return evaluations

    @classmethod
    def _first_touch_evaluation(
        cls,
        *,
        prediction: PredictionRow,
        snapshots: list[MarketSnapshotRow],
    ) -> PredictionEvaluation | None:
        direction = PredictionDirection(prediction.direction)
        tp_price, sl_price = cls._barrier_prices(
            prediction=prediction,
            entry_price=prediction.entry_price,
            direction=direction,
        )

        for snapshot in snapshots:
            if snapshot.price is None:
                continue

            price = snapshot.price

            if direction == PredictionDirection.LONG:
                if price >= tp_price:
                    return cls._barrier_evaluation(
                        prediction=prediction,
                        evaluated_at=snapshot.timestamp,
                        exit_price=price,
                        outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
                        label=1,
                        success=True,
                    )
                if price <= sl_price:
                    return cls._barrier_evaluation(
                        prediction=prediction,
                        evaluated_at=snapshot.timestamp,
                        exit_price=price,
                        outcome=PredictionEvaluationOutcome.STOP_LOSS,
                        label=-1,
                        success=False,
                    )
            else:
                if price <= tp_price:
                    return cls._barrier_evaluation(
                        prediction=prediction,
                        evaluated_at=snapshot.timestamp,
                        exit_price=price,
                        outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
                        label=1,
                        success=True,
                    )
                if price >= sl_price:
                    return cls._barrier_evaluation(
                        prediction=prediction,
                        evaluated_at=snapshot.timestamp,
                        exit_price=price,
                        outcome=PredictionEvaluationOutcome.STOP_LOSS,
                        label=-1,
                        success=False,
                    )

        return None

    @classmethod
    def _barrier_prices(
        cls,
        *,
        prediction: PredictionRow,
        entry_price: Decimal,
        direction: PredictionDirection,
    ) -> tuple[Decimal, Decimal]:
        take_profit_pct = prediction.take_profit_pct or Decimal("0.60")
        stop_loss_pct = prediction.stop_loss_pct or Decimal("0.30")
        tp_fraction = take_profit_pct / Decimal("100")
        sl_fraction = stop_loss_pct / Decimal("100")

        if direction == PredictionDirection.LONG:
            return (
                entry_price * (Decimal("1") + tp_fraction),
                entry_price * (Decimal("1") - sl_fraction),
            )

        return (
            entry_price * (Decimal("1") - tp_fraction),
            entry_price * (Decimal("1") + sl_fraction),
        )

    @classmethod
    def _barrier_evaluation(
        cls,
        *,
        prediction: PredictionRow,
        evaluated_at: datetime,
        exit_price: Decimal,
        outcome: PredictionEvaluationOutcome,
        label: int,
        success: bool,
    ) -> PredictionEvaluation:
        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            outcome=outcome,
            label=label,
            evaluated_at=evaluated_at,
            exit_price=exit_price,
            return_pct=cls._directional_return_pct(
                prediction=prediction,
                exit_price=exit_price,
            ),
            success=success,
            evaluation_source="persisted_snapshot",
            evaluation_version="first_touch_v1",
        )

    @classmethod
    def _expired_no_touch_evaluation(
        cls,
        *,
        prediction: PredictionRow,
        evaluated_at: datetime,
        exit_price: Decimal,
    ) -> PredictionEvaluation:
        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            outcome=PredictionEvaluationOutcome.EXPIRED_NO_TOUCH,
            label=0,
            evaluated_at=evaluated_at,
            exit_price=exit_price,
            return_pct=cls._directional_return_pct(
                prediction=prediction,
                exit_price=exit_price,
            ),
            success=None,
            evaluation_source="persisted_snapshot",
            evaluation_version="first_touch_v1",
        )

    @staticmethod
    def _directional_return_pct(
        *,
        prediction: PredictionRow,
        exit_price: Decimal,
    ) -> Decimal:
        raw_return = (
            (exit_price - prediction.entry_price)
            / prediction.entry_price
        ) * Decimal("100")

        direction = PredictionDirection(prediction.direction)
        return (
            raw_return
            if direction == PredictionDirection.LONG
            else -raw_return
        )

    async def evaluation_provenance_stats(
        self,
        *,
        symbol: str | None = None,
    ) -> list[EvaluationProvenanceStats]:
        async with self._session_factory() as session:
            query = (
                select(
                    PredictionEvaluationRow.evaluation_source,
                    PredictionEvaluationRow.evaluation_version,
                    func.count(PredictionEvaluationRow.prediction_id),
                    func.sum(
                        (
                            PredictionEvaluationRow.outcome
                            == PredictionEvaluationOutcome.TAKE_PROFIT.value
                        ).cast(Integer)
                    ),
                    func.sum(
                        (
                            PredictionEvaluationRow.outcome
                            == PredictionEvaluationOutcome.STOP_LOSS.value
                        ).cast(Integer)
                    ),
                    func.sum(
                        (
                            PredictionEvaluationRow.outcome
                            == PredictionEvaluationOutcome.EXPIRED_NO_TOUCH.value
                        ).cast(Integer)
                    ),
                    func.sum(
                        (
                            PredictionEvaluationRow.outcome
                            == PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA.value
                        ).cast(Integer)
                    ),
                    func.sum(
                        (
                            PredictionEvaluationRow.outcome.is_(None)
                        ).cast(Integer)
                    ),
                )
                .join(
                    PredictionRow,
                    PredictionRow.id == PredictionEvaluationRow.prediction_id,
                )
                .group_by(
                    PredictionEvaluationRow.evaluation_source,
                    PredictionEvaluationRow.evaluation_version,
                )
                .order_by(
                    PredictionEvaluationRow.evaluation_source,
                    PredictionEvaluationRow.evaluation_version,
                )
            )

            if symbol:
                query = query.where(
                    PredictionRow.symbol == symbol.upper()
                )

            rows = (await session.execute(query)).all()

            return [
                EvaluationProvenanceStats(
                    evaluation_source=row[0],
                    evaluation_version=row[1],
                    total=int(row[2] or 0),
                    take_profit=int(row[3] or 0),
                    stop_loss=int(row[4] or 0),
                    expired_no_touch=int(row[5] or 0),
                    expired_without_data=int(row[6] or 0),
                    unresolved=int(row[7] or 0),
                )
                for row in rows
            ]

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
