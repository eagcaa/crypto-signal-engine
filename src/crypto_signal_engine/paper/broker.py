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
        self._peak_equity = self._equity
        self._max_drawdown_pct = Decimal("0")
        self._consecutive_losses = 0
        self._trading_halted = False
        self._halt_reason: str | None = None
        self._realized_pnl = Decimal("0")
        self._positions: dict[UUID, PaperPosition] = {}

    @property
    def positions(self) -> tuple[PaperPosition, ...]:
        return tuple(self._positions.values())

    def restore(
        self,
        positions: list[PaperPosition],
    ) -> None:
        self._positions = {
            position.prediction_id: position
            for position in positions
        }
        self._realized_pnl = sum(
            (
                position.pnl or Decimal("0")
                for position in positions
                if position.status == PaperPositionStatus.CLOSED
            ),
            Decimal("0"),
        )
        self._equity = (
            self._risk.config.starting_equity
            + self._realized_pnl
        )
        self._rebuild_risk_state()

    def open_from_prediction(
        self,
        prediction: Prediction,
    ) -> PaperPosition | None:
        if self._trading_halted:
            return None

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
        self._update_risk_state(pnl)
        return position

    def snapshot(self) -> PaperAccountSnapshot:
        closed = [
            position
            for position in self._positions.values()
            if position.status == PaperPositionStatus.CLOSED
        ]
        wins = sum(
            1
            for position in closed
            if (position.pnl or Decimal("0")) > 0
        )
        losses = sum(
            1
            for position in closed
            if (position.pnl or Decimal("0")) < 0
        )
        win_rate = (
            Decimal(wins) / Decimal(len(closed)) * Decimal("100")
            if closed
            else None
        )

        return PaperAccountSnapshot(
            equity=self._equity,
            realized_pnl=self._realized_pnl,
            open_positions=self._open_count(),
            closed_positions=len(closed),
            invalidated_positions=sum(
                1
                for position in self._positions.values()
                if position.status == PaperPositionStatus.INVALIDATED
            ),
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            peak_equity=self._peak_equity,
            drawdown_pct=self._current_drawdown_pct(),
            max_drawdown_pct=self._max_drawdown_pct,
            consecutive_losses=self._consecutive_losses,
            trading_halted=self._trading_halted,
            halt_reason=self._halt_reason,
        )


    def _update_risk_state(self, pnl: Decimal) -> None:
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity

        drawdown = self._current_drawdown_pct()
        self._max_drawdown_pct = max(self._max_drawdown_pct, drawdown)

        if pnl < 0:
            self._consecutive_losses += 1
        elif pnl > 0:
            self._consecutive_losses = 0

        if drawdown >= self._risk.config.max_drawdown_pct:
            self._trading_halted = True
            self._halt_reason = (
                f"max_drawdown_reached:{drawdown:.4f}%"
            )
        elif (
            self._consecutive_losses
            >= self._risk.config.max_consecutive_losses
        ):
            self._trading_halted = True
            self._halt_reason = (
                "max_consecutive_losses_reached:"
                f"{self._consecutive_losses}"
            )

    def _rebuild_risk_state(self) -> None:
        self._peak_equity = self._risk.config.starting_equity
        self._max_drawdown_pct = Decimal("0")
        self._consecutive_losses = 0
        self._trading_halted = False
        self._halt_reason = None

        equity = self._risk.config.starting_equity
        closed = sorted(
            (
                position
                for position in self._positions.values()
                if (
                    position.status == PaperPositionStatus.CLOSED
                    and position.closed_at is not None
                )
            ),
            key=lambda position: position.closed_at,
        )

        for position in closed:
            pnl = position.pnl or Decimal("0")
            equity += pnl
            if equity > self._peak_equity:
                self._peak_equity = equity

            drawdown = (
                (self._peak_equity - equity)
                / self._peak_equity
                * Decimal("100")
                if self._peak_equity > 0
                else Decimal("0")
            )
            self._max_drawdown_pct = max(
                self._max_drawdown_pct,
                drawdown,
            )

            if pnl < 0:
                self._consecutive_losses += 1
            elif pnl > 0:
                self._consecutive_losses = 0

            if (
                not self._trading_halted
                and drawdown >= self._risk.config.max_drawdown_pct
            ):
                self._trading_halted = True
                self._halt_reason = (
                    f"max_drawdown_reached:{drawdown:.4f}%"
                )
            elif (
                not self._trading_halted
                and self._consecutive_losses
                >= self._risk.config.max_consecutive_losses
            ):
                self._trading_halted = True
                self._halt_reason = (
                    "max_consecutive_losses_reached:"
                    f"{self._consecutive_losses}"
                )

    def _current_drawdown_pct(self) -> Decimal:
        if self._peak_equity <= 0:
            return Decimal("0")
        return max(
            Decimal("0"),
            (self._peak_equity - self._equity)
            / self._peak_equity
            * Decimal("100"),
        )

    def _open_count(self) -> int:
        return sum(
            1
            for position in self._positions.values()
            if position.status == PaperPositionStatus.OPEN
        )
