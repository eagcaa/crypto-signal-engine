from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    build_agreement_edge_stats,
    build_feature_edge_stats,
)


def _prediction(
    *,
    direction: PredictionDirection,
    contributions: dict[str, Decimal],
) -> Prediction:
    created_at = datetime(2026, 10, 9, 6, 30, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30") if direction == PredictionDirection.LONG else Decimal("-0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
        feature_contributions=contributions,
    )


def test_feature_edge_marks_support_and_opposition_by_direction() -> None:
    prediction = _prediction(
        direction=PredictionDirection.SHORT,
        contributions={
            "trend": Decimal("-0.10"),
            "order_book": Decimal("0.05"),
        },
    )
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
            price=Decimal("99.80"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("100.05"),
        ),
    ]

    rows = build_feature_edge_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )
    relations = {(row.feature_name, row.relation) for row in rows}

    assert ("trend", "support") in relations
    assert ("order_book", "oppose") in relations


def test_agreement_edge_counts_supporting_features() -> None:
    prediction = _prediction(
        direction=PredictionDirection.LONG,
        contributions={
            "trend": Decimal("0.10"),
            "order_book": Decimal("0.05"),
            "funding": Decimal("-0.02"),
            "open_interest": Decimal("0"),
        },
    )
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
            price=Decimal("100.20"),
        )
    ]

    rows = build_agreement_edge_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert len(rows) == 1
    assert rows[0].supporting_features == 2
    assert rows[0].cost_clear_rate_pct == Decimal("100")
