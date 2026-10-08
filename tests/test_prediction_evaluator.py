from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.db.models import MarketSnapshotRow, PredictionRow
from crypto_signal_engine.db.prediction_repository import PredictionRepository
from crypto_signal_engine.predictions import (
    PredictionEvaluationOutcome,
)


def make_prediction(
    *,
    direction: str,
    entry_price: str = "100",
) -> PredictionRow:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return PredictionRow(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=direction,
        entry_price=Decimal(entry_price),
        raw_score=Decimal("0.4"),
        data_quality=Decimal("0.83"),
        model_name="test",
        feature_contributions_json=None,
        reason=None,
    )


def market_snapshot(
    prediction: PredictionRow,
    *,
    seconds: int,
    price: str,
) -> MarketSnapshotRow:
    return MarketSnapshotRow(
        timestamp=prediction.created_at + timedelta(seconds=seconds),
        symbol=prediction.symbol,
        price=Decimal(price),
        data_quality=Decimal("0.83"),
    )


def test_long_take_profit_is_first_touch_label_plus_one() -> None:
    prediction = make_prediction(direction="long")
    snapshots = [
        market_snapshot(prediction, seconds=5, price="100.20"),
        market_snapshot(prediction, seconds=10, price="100.61"),
        market_snapshot(prediction, seconds=15, price="99.60"),
    ]

    evaluation = PredictionRepository._first_touch_evaluation(
        prediction=prediction,
        snapshots=snapshots,
    )

    assert evaluation is not None
    assert evaluation.outcome == PredictionEvaluationOutcome.TAKE_PROFIT
    assert evaluation.label == 1
    assert evaluation.success is True
    assert evaluation.evaluated_at == snapshots[1].timestamp
    assert evaluation.evaluation_source == "persisted_snapshot"
    assert evaluation.evaluation_version == "first_touch_v1"


def test_long_stop_loss_is_first_touch_label_minus_one() -> None:
    prediction = make_prediction(direction="long")
    snapshots = [
        market_snapshot(prediction, seconds=5, price="99.80"),
        market_snapshot(prediction, seconds=10, price="99.69"),
        market_snapshot(prediction, seconds=15, price="100.70"),
    ]

    evaluation = PredictionRepository._first_touch_evaluation(
        prediction=prediction,
        snapshots=snapshots,
    )

    assert evaluation is not None
    assert evaluation.outcome == PredictionEvaluationOutcome.STOP_LOSS
    assert evaluation.label == -1
    assert evaluation.success is False
    assert evaluation.evaluated_at == snapshots[1].timestamp


def test_short_take_profit_and_stop_loss_prices_are_directional() -> None:
    prediction = make_prediction(direction="short")

    tp_price, sl_price = PredictionRepository._barrier_prices(
        entry_price=prediction.entry_price,
        direction=prediction.direction,
    )

    assert tp_price == Decimal("99.400")
    assert sl_price == Decimal("100.300")


def test_expired_without_touch_gets_zero_label() -> None:
    prediction = make_prediction(direction="short")

    evaluation = PredictionRepository._expired_no_touch_evaluation(
        prediction=prediction,
        evaluated_at=prediction.expires_at,
        exit_price=Decimal("99.90"),
    )

    assert evaluation.outcome == PredictionEvaluationOutcome.EXPIRED_NO_TOUCH
    assert evaluation.label == 0
    assert evaluation.success is None
    assert evaluation.return_pct == Decimal("0.100")
