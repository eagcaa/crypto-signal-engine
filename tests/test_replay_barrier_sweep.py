from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    build_barrier_sweep,
    top_barrier_sweep_rows,
)


def _prediction(direction: PredictionDirection) -> Prediction:
    created_at = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
    )


def test_barrier_sweep_uses_first_touch_and_execution_costs() -> None:
    prediction = _prediction(PredictionDirection.LONG)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("100.16"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("99.70"),
        ),
    ]

    rows = build_barrier_sweep(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
        take_profit_grid=(Decimal("0.15"),),
        stop_loss_grid=(Decimal("0.20"),),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.take_profit == 1
    assert row.stop_loss == 0
    assert row.no_touch == 0
    assert row.expectancy_pct == Decimal("0.03")


def test_barrier_sweep_closes_no_touch_at_last_price() -> None:
    prediction = _prediction(PredictionDirection.SHORT)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("99.95"),
        ),
    ]

    row = build_barrier_sweep(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
        take_profit_grid=(Decimal("0.20"),),
        stop_loss_grid=(Decimal("0.20"),),
    )[0]

    assert row.no_touch == 1
    assert row.expectancy_pct == Decimal("-0.07")


def test_top_barrier_rows_rank_by_expectancy() -> None:
    prediction = _prediction(PredictionDirection.LONG)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("100.21"),
        ),
    ]

    rows = build_barrier_sweep(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
        take_profit_grid=(Decimal("0.15"), Decimal("0.20")),
        stop_loss_grid=(Decimal("0.20"),),
    )
    top = top_barrier_sweep_rows(rows, per_group=1)

    assert len(top) == 1
    assert top[0].take_profit_pct == Decimal("0.20")
    assert top[0].expectancy_pct == Decimal("0.08")
