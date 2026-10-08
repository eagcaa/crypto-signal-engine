import asyncio
from decimal import Decimal

from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    TradeTick,
)
from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)


class LiveFirstTouchEvaluator:
    """Evaluate open predictions on every canonical Binance spot trade.

    This closes the gap left by 5-second persisted market snapshots. The
    snapshot-based evaluator remains as a restart/replay/fallback path.
    """

    TAKE_PROFIT_PCT = Decimal("0.60")
    STOP_LOSS_PCT = Decimal("0.30")

    def __init__(self) -> None:
        self._predictions: dict[object, Prediction] = {}
        self._lock = asyncio.Lock()

    async def register(self, prediction: Prediction) -> None:
        async with self._lock:
            self._predictions[prediction.id] = prediction

    async def process_trade(
        self,
        trade: TradeTick,
    ) -> list[PredictionEvaluation]:
        if (
            trade.exchange != Exchange.BINANCE
            or trade.market_type != MarketType.SPOT
        ):
            return []

        async with self._lock:
            evaluations: list[PredictionEvaluation] = []
            finished_ids: list[object] = []

            for prediction_id, prediction in self._predictions.items():
                if prediction.symbol.upper() != trade.symbol.upper():
                    continue

                if trade.event_time <= prediction.created_at:
                    continue

                if trade.event_time > prediction.expires_at:
                    finished_ids.append(prediction_id)
                    continue

                evaluation = self._evaluate_price(
                    prediction=prediction,
                    price=trade.price,
                    evaluated_at=trade.event_time,
                )
                if evaluation is not None:
                    evaluations.append(evaluation)
                    finished_ids.append(prediction_id)

            for prediction_id in finished_ids:
                self._predictions.pop(prediction_id, None)

            return evaluations

    @classmethod
    def _evaluate_price(
        cls,
        *,
        prediction: Prediction,
        price: Decimal,
        evaluated_at,
    ) -> PredictionEvaluation | None:
        tp_price, sl_price = cls._barrier_prices(prediction)

        if prediction.direction == PredictionDirection.LONG:
            if price >= tp_price:
                return cls._build_evaluation(
                    prediction=prediction,
                    price=price,
                    evaluated_at=evaluated_at,
                    outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
                    label=1,
                    success=True,
                )
            if price <= sl_price:
                return cls._build_evaluation(
                    prediction=prediction,
                    price=price,
                    evaluated_at=evaluated_at,
                    outcome=PredictionEvaluationOutcome.STOP_LOSS,
                    label=-1,
                    success=False,
                )
        else:
            if price <= tp_price:
                return cls._build_evaluation(
                    prediction=prediction,
                    price=price,
                    evaluated_at=evaluated_at,
                    outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
                    label=1,
                    success=True,
                )
            if price >= sl_price:
                return cls._build_evaluation(
                    prediction=prediction,
                    price=price,
                    evaluated_at=evaluated_at,
                    outcome=PredictionEvaluationOutcome.STOP_LOSS,
                    label=-1,
                    success=False,
                )

        return None

    @classmethod
    def _barrier_prices(
        cls,
        prediction: Prediction,
    ) -> tuple[Decimal, Decimal]:
        tp_fraction = cls.TAKE_PROFIT_PCT / Decimal("100")
        sl_fraction = cls.STOP_LOSS_PCT / Decimal("100")

        if prediction.direction == PredictionDirection.LONG:
            return (
                prediction.entry_price * (Decimal("1") + tp_fraction),
                prediction.entry_price * (Decimal("1") - sl_fraction),
            )

        return (
            prediction.entry_price * (Decimal("1") - tp_fraction),
            prediction.entry_price * (Decimal("1") + sl_fraction),
        )

    @classmethod
    def _build_evaluation(
        cls,
        *,
        prediction: Prediction,
        price: Decimal,
        evaluated_at,
        outcome: PredictionEvaluationOutcome,
        label: int,
        success: bool,
    ) -> PredictionEvaluation:
        raw_return = (
            (price - prediction.entry_price)
            / prediction.entry_price
        ) * Decimal("100")

        directional_return = (
            raw_return
            if prediction.direction == PredictionDirection.LONG
            else -raw_return
        )

        return PredictionEvaluation(
            prediction_id=prediction.id,
            status=PredictionEvaluationStatus.EVALUATED,
            outcome=outcome,
            label=label,
            evaluated_at=evaluated_at,
            exit_price=price,
            return_pct=directional_return,
            success=success,
        )
