from decimal import Decimal
from uuid import UUID, uuid4

from crypto_signal_engine.paper.models import (
    PaperAccountSnapshot,
    PaperPosition,
    PaperPositionStatus,
    PaperRiskConfig,
)
from crypto_signal_engine.paper.risk import PaperRiskManager
from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
)


class PaperBroker:
    """Paper-only execution/accounting engine.

    It never calls an exchange API. Positions are opened from persisted
    predictions and closed from their measured evaluation outcome.
    """

    def __init__(
        self,
        risk_config: PaperRiskConfig | None = None,
    ) -> None:
        self._risk = PaperRiskManager(risk_config)
        self._equity = self._risk.config.starting_equity
        self._realized_pnl = Decimal("0")
        self._positions: dict[UUID, PaperPosition] = {}

    @property
    def positions(self) -> tuple[PaperPosition, ...]:
        return tuple(self._positions.values())

    def open_from_prediction(
        self,
        prediction: Prediction,
    ) -> PaperPosition | None:
        if self._open_count() >= self._risk.config.max_open_positions:
            return None

        if any(
            position.status == PaperPositionStatus.OPEN
            and position.symbol.upper() == prediction.symbol.upper()
            for position in self._positions.values()
        ):
            return None

        notional = self._risk.position_notional(equity=self._equity)
        if notional <= 0 or prediction.entry_price <= 0:
            return None

        position = PaperPosition(
            id=uuid4(),
            prediction_id=prediction.id,
            symbol=prediction.symbol,
            horizon_seconds=prediction.horizon_seconds,
            direction=prediction.direction.value,
            opened_at=prediction.created_at,
            entry_price=prediction.entry_price,
            notional=notional,
            quantity=notional / prediction.entry_price,
        )
        self._positions[prediction.id] = position
        return position

    def apply_evaluation(
        self,
        evaluation: PredictionEvaluation,
    ) -> PaperPosition | None:
        position = self._positions.get(evaluation.prediction_id)
        if position is None or position.status != PaperPositionStatus.OPEN:
            return None

        if (
            evaluation.outcome
            == PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA
            or evaluation.return_pct is None
            or evaluation.exit_price is None
        ):
            position.status = PaperPositionStatus.INVALIDATED
            position.closed_at = evaluation.evaluated_at
            position.close_reason = evaluation.outcome.value
            return position

        pnl = (
            position.notional
            * evaluation.return_pct
            / Decimal("100")
        )

        position.status = PaperPositionStatus.CLOSED
        position.closed_at = evaluation.evaluated_at
        position.exit_price = evaluation.exit_price
        position.return_pct = evaluation.return_pct
        position.pnl = pnl
        position.close_reason = evaluation.outcome.value

        self._realized_pnl += pnl
        self._equity += pnl
        return position

    def snapshot(self) -> PaperAccountSnapshot:
        return PaperAccountSnapshot(
            equity=self._equity,
            realized_pnl=self._realized_pnl,
            open_positions=self._open_count(),
            closed_positions=sum(
                1
                for position in self._positions.values()
                if position.status == PaperPositionStatus.CLOSED
            ),
            invalidated_positions=sum(
                1
                for position in self._positions.values()
                if position.status == PaperPositionStatus.INVALIDATED
            ),
        )

    def _open_count(self) -> int:
        return sum(
            1
            for position in self._positions.values()
            if position.status == PaperPositionStatus.OPEN
        )
