from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.paper import (
    PaperRiskConfig,
    build_paper_performance_report,
    simulate_replay,
    simulate_replay_broker,
)
from crypto_signal_engine.predictions import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)
from crypto_signal_engine.replay.models import ReplayResult


def test_simulate_replay_tracks_realized_pnl() -> None:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    prediction = Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.SHORT,
        entry_price=Decimal("100"),
        raw_score=Decimal("-0.35"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v3_5m",
    )
    evaluation = PredictionEvaluation(
        prediction_id=prediction.id,
        status=PredictionEvaluationStatus.EVALUATED,
        outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
        label=1,
        evaluated_at=created_at + timedelta(seconds=30),
        exit_price=Decimal("99.40"),
        return_pct=Decimal("0.60"),
        success=True,
    )

    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(evaluation,),
        open_predictions=(),
    )

    snapshot = simulate_replay(
        result,
        risk_config=PaperRiskConfig(
            starting_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.50"),
            max_notional_pct=Decimal("10"),
            max_open_positions=1,
            stop_loss_pct=Decimal("0.30"),
        ),
    )

    assert snapshot.realized_pnl == Decimal("6.00")
    assert snapshot.equity == Decimal("10006.00")
    assert snapshot.closed_positions == 1



def test_simulate_replay_broker_exposes_positions_for_reporting() -> None:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    prediction = Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v3_5m",
    )
    evaluation = PredictionEvaluation(
        prediction_id=prediction.id,
        status=PredictionEvaluationStatus.EVALUATED,
        outcome=PredictionEvaluationOutcome.STOP_LOSS,
        label=-1,
        evaluated_at=created_at + timedelta(seconds=30),
        exit_price=Decimal("99.70"),
        return_pct=Decimal("-0.30"),
        success=False,
    )
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(evaluation,),
        open_predictions=(),
    )

    broker = simulate_replay_broker(
        result,
        risk_config=PaperRiskConfig(
            starting_equity=Decimal("10000"),
            max_notional_pct=Decimal("10"),
        ),
    )
    report = build_paper_performance_report(
        list(broker.positions)
    )

    assert len(broker.positions) == 1
    assert report.overall.trades == 1
    assert report.overall.losses == 1
    assert report.overall.net_pnl == Decimal("-3.00")
