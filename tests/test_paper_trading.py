from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.paper import (
    PaperBroker,
    PaperPositionStatus,
    PaperRiskConfig,
    PaperRiskManager,
)
from crypto_signal_engine.predictions import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)


def make_prediction(*, symbol: str = "BTCUSDT") -> Prediction:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol=symbol,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v3_5m",
    )


def test_risk_manager_caps_notional_by_equity_fraction() -> None:
    manager = PaperRiskManager(
        PaperRiskConfig(
            starting_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("1"),
            max_notional_pct=Decimal("10"),
            stop_loss_pct=Decimal("0.30"),
        )
    )

    assert manager.position_notional(equity=Decimal("10000")) == Decimal("1000")


def test_paper_broker_opens_and_closes_profitable_position() -> None:
    broker = PaperBroker(
        PaperRiskConfig(
            starting_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.50"),
            max_notional_pct=Decimal("10"),
            max_open_positions=1,
            stop_loss_pct=Decimal("0.30"),
        )
    )
    prediction = make_prediction()
    position = broker.open_from_prediction(prediction)

    assert position is not None
    assert position.notional == Decimal("1000")
    assert position.quantity == Decimal("10")

    evaluation = PredictionEvaluation(
        prediction_id=prediction.id,
        status=PredictionEvaluationStatus.EVALUATED,
        outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
        label=1,
        evaluated_at=prediction.created_at + timedelta(seconds=30),
        exit_price=Decimal("100.60"),
        return_pct=Decimal("0.60"),
        success=True,
    )
    closed = broker.apply_evaluation(evaluation)

    assert closed is not None
    assert closed.status == PaperPositionStatus.CLOSED
    assert closed.pnl == Decimal("6.00")

    snapshot = broker.snapshot()
    assert snapshot.equity == Decimal("10006.00")
    assert snapshot.realized_pnl == Decimal("6.00")
    assert snapshot.closed_positions == 1


def test_paper_broker_rejects_second_position_while_one_is_open() -> None:
    broker = PaperBroker(
        PaperRiskConfig(max_open_positions=1)
    )

    first = broker.open_from_prediction(make_prediction(symbol="BTCUSDT"))
    second = broker.open_from_prediction(make_prediction(symbol="ETHUSDT"))

    assert first is not None
    assert second is None


def test_paper_broker_invalidates_position_when_evaluation_has_no_data() -> None:
    broker = PaperBroker()
    prediction = make_prediction()
    broker.open_from_prediction(prediction)

    evaluation = PredictionEvaluation(
        prediction_id=prediction.id,
        status=PredictionEvaluationStatus.EXPIRED_WITHOUT_DATA,
        outcome=PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA,
        label=None,
        evaluated_at=prediction.expires_at,
        exit_price=None,
        return_pct=None,
        success=None,
    )

    position = broker.apply_evaluation(evaluation)

    assert position is not None
    assert position.status == PaperPositionStatus.INVALIDATED
    assert broker.snapshot().realized_pnl == Decimal("0")
