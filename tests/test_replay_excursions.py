from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    build_excursion_stats,
)


def _prediction(
    *,
    direction: PredictionDirection,
    horizon_seconds: int = 300,
) -> Prediction:
    created_at = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=horizon_seconds),
        horizon_seconds=horizon_seconds,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
    )


def test_excursion_stats_measure_full_horizon_mfe_and_mae() -> None:
    prediction = _prediction(direction=PredictionDirection.LONG)
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
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("99.70"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=40),
            price=Decimal("100.50"),
        ),
    ]

    rows = build_excursion_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.median_mfe_pct == Decimal("0.500")
    assert row.median_mae_pct == Decimal("0.300")
    assert row.cost_clear_rate_pct == Decimal("100")


def test_excursion_stats_handle_short_direction() -> None:
    prediction = _prediction(direction=PredictionDirection.SHORT)
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
            price=Decimal("99.60"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("100.25"),
        ),
    ]

    row = build_excursion_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )[0]

    assert row.median_mfe_pct == Decimal("0.400")
    assert row.median_mae_pct == Decimal("0.250")
