from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.predictions.engine import CompositePredictionEngine
from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(slots=True)
class _OpenPrediction:
    prediction: Prediction
    last_price: Decimal | None = None
    last_price_at: datetime | None = None


class ReplayRunner:
    """Run the live prediction engine against historical feature snapshots.

    Price points are evaluated in timestamp order and use the same first-touch
    TP/SL semantics as live research. For precise replay, callers should pass
    trade-level canonical Binance spot prices. Snapshot/candle prices are
    supported but are necessarily lower-resolution approximations.
    """

    TAKE_PROFIT_PCT = Decimal("0.60")
    STOP_LOSS_PCT = Decimal("0.30")

    def __init__(
        self,
        prediction_engine: CompositePredictionEngine | None = None,
        *,
        horizons: tuple[int, ...] = (300, 900),
        prediction_interval_seconds: int = 60,
    ) -> None:
        self._prediction_engine = prediction_engine or CompositePredictionEngine()
        self._horizons = horizons
        self._prediction_interval = timedelta(seconds=prediction_interval_seconds)

    def run(
        self,
        feature_snapshots: Iterable[ResearchFeatureSnapshot],
        price_points: Iterable[ReplayPricePoint],
    ) -> ReplayResult:
        features = sorted(feature_snapshots, key=lambda item: item.timestamp)
        prices = sorted(price_points, key=lambda item: item.timestamp)

        decisions = []
        predictions = []
        evaluations = []
        open_predictions: dict[object, _OpenPrediction] = {}
        last_prediction_at: dict[str, datetime] = {}

        events: list[tuple[datetime, int, object]] = []
        events.extend((point.timestamp, 0, point) for point in prices)
        events.extend((snapshot.timestamp, 1, snapshot) for snapshot in features)
        events.sort(key=lambda item: (item[0], item[1]))

        for timestamp, event_type, payload in events:
            evaluations.extend(
                self._expire_before(
                    open_predictions,
                    timestamp,
                )
            )

            if event_type == 0:
                point = payload
                assert isinstance(point, ReplayPricePoint)
                evaluations.extend(
                    self._process_price(
                        open_predictions,
                        point,
                    )
                )
                continue

            snapshot = payload
            assert isinstance(snapshot, ResearchFeatureSnapshot)

            previous_prediction_at = last_prediction_at.get(snapshot.symbol)
            should_generate = (
                previous_prediction_at is None
                or snapshot.timestamp - previous_prediction_at >= self._prediction_interval
            )
            if not should_generate:
                continue

            for horizon_seconds in self._horizons:
                decision = self._prediction_engine.decide(
                    snapshot,
                    horizon_seconds=horizon_seconds,
                )
                decisions.append(decision)

                if decision.prediction is not None:
                    prediction = decision.prediction
                    predictions.append(prediction)
                    open_predictions[prediction.id] = _OpenPrediction(prediction)

            last_prediction_at[snapshot.symbol] = snapshot.timestamp

        if events:
            final_timestamp = events[-1][0]
            evaluations.extend(
                self._expire_before(
                    open_predictions,
                    final_timestamp + timedelta(microseconds=1),
                )
            )

        return ReplayResult(
            decisions=tuple(decisions),
            predictions=tuple(predictions),
            evaluations=tuple(evaluations),
            open_predictions=tuple(
                state.prediction for state in open_predictions.values()
            ),
        )

    def _process_price(
        self,
        open_predictions: dict[object, _OpenPrediction],
        point: ReplayPricePoint,
    ) -> list[PredictionEvaluation]:
        evaluations = []
        finished_ids = []

        for prediction_id, state in open_predictions.items():
            prediction = state.prediction
            if prediction.symbol.upper() != point.symbol.upper():
                continue
            if point.timestamp <= prediction.created_at:
                continue
            if point.timestamp > prediction.expires_at:
                continue

            state.last_price = point.price
            state.last_price_at = point.timestamp

            evaluation = self._evaluate_touch(
                prediction,
                point.price,
                point.timestamp,
            )
            if evaluation is not None:
                evaluations.append(evaluation)
                finished_ids.append(prediction_id)

        for prediction_id in finished_ids:
            open_predictions.pop(prediction_id, None)

        return evaluations

    def _expire_before(
        self,
        open_predictions: dict[object, _OpenPrediction],
        timestamp: datetime,
    ) -> list[PredictionEvaluation]:
        evaluations = []
        finished_ids = []

        for prediction_id, state in open_predictions.items():
            prediction = state.prediction
            if prediction.expires_at >= timestamp:
                continue

            evaluations.append(self._expired_evaluation(state))
            finished_ids.append(prediction_id)

        for prediction_id in finished_ids:
            open_predictions.pop(prediction_id, None)

        return evaluations

    def _expired_evaluation(
        self,
        state: _OpenPrediction,
    ) -> PredictionEvaluation:
        prediction = state.prediction

        if state.last_price is None:
            return PredictionEvaluation(
                prediction_id=prediction.id,
                status=PredictionEvaluationStatus.EXPIRED_WITHOUT_DATA,
                outcome=PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA,
                label=None,
                evaluated_at=prediction.expires_at,
                exit_price=None,
                return_pct=None,
                success=None,
            )

        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            outcome=PredictionEvaluationOutcome.EXPIRED_NO_TOUCH,
            label=0,
            evaluated_at=prediction.expires_at,
            exit_price=state.last_price,
            return_pct=self._directional_return_pct(
                prediction,
                state.last_price,
            ),
            success=None,
        )

    @classmethod
    def _evaluate_touch(
        cls,
        prediction: Prediction,
        price: Decimal,
        evaluated_at: datetime,
    ) -> PredictionEvaluation | None:
        tp_fraction = cls.TAKE_PROFIT_PCT / Decimal("100")
        sl_fraction = cls.STOP_LOSS_PCT / Decimal("100")

        if prediction.direction == PredictionDirection.LONG:
            take_profit = prediction.entry_price * (Decimal("1") + tp_fraction)
            stop_loss = prediction.entry_price * (Decimal("1") - sl_fraction)
            outcome = (
                PredictionEvaluationOutcome.TAKE_PROFIT
                if price >= take_profit
                else (
                    PredictionEvaluationOutcome.STOP_LOSS
                    if price <= stop_loss
                    else None
                )
            )
        else:
            take_profit = prediction.entry_price * (Decimal("1") - tp_fraction)
            stop_loss = prediction.entry_price * (Decimal("1") + sl_fraction)
            outcome = (
                PredictionEvaluationOutcome.TAKE_PROFIT
                if price <= take_profit
                else (
                    PredictionEvaluationOutcome.STOP_LOSS
                    if price >= stop_loss
                    else None
                )
            )

        if outcome is None:
            return None

        is_take_profit = outcome == PredictionEvaluationOutcome.TAKE_PROFIT
        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            outcome=outcome,
            label=1 if is_take_profit else -1,
            evaluated_at=evaluated_at,
            exit_price=price,
            return_pct=cls._directional_return_pct(prediction, price),
            success=is_take_profit,
        )

    @staticmethod
    def _directional_return_pct(
        prediction: Prediction,
        price: Decimal,
    ) -> Decimal:
        raw_return = (
            (price - prediction.entry_price)
            / prediction.entry_price
            * Decimal("100")
        )
        if prediction.direction == PredictionDirection.SHORT:
            return -raw_return
        return raw_return
