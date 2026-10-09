from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    bootstrap_candidate_robustness,
    build_candidate_independent_returns,
    build_candidate_trade_returns,
)


def _prediction(direction: PredictionDirection) -> Prediction:
    created_at = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
        horizon_seconds=900,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.90"),
        model_name="test",
    )


def test_candidate_trade_returns_use_first_touch_and_costs() -> None:
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
            price=Decimal("100.41"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("99.70"),
        ),
    ]

    returns = build_candidate_trade_returns(
        result,
        points,
        take_profit_pct=Decimal("0.40"),
        stop_loss_pct=Decimal("0.18"),
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert returns == (Decimal("0.28"),)


def test_bootstrap_candidate_robustness_passes_consistent_positive_returns() -> None:
    returns = tuple(Decimal("0.20") for _ in range(30))

    result = bootstrap_candidate_robustness(
        returns,
        simulations=200,
        seed=7,
        minimum_positive_expectancy_rate=Decimal("0.80"),
        minimum_samples=20,
    )

    assert result.samples == 30
    assert result.positive_expectancy_rate == Decimal("1")
    assert result.p05_expectancy_pct == Decimal("0.20")
    assert result.passed is True
    assert result.reasons == ()


def test_bootstrap_candidate_robustness_holds_small_or_unstable_sample() -> None:
    returns = (
        Decimal("0.28"),
        Decimal("-0.30"),
        Decimal("0.28"),
        Decimal("-0.30"),
    )

    result = bootstrap_candidate_robustness(
        returns,
        simulations=200,
        seed=11,
        minimum_positive_expectancy_rate=Decimal("0.80"),
        minimum_samples=20,
    )

    assert result.passed is False
    assert "insufficient_independent_samples:4/20" in result.reasons
    assert any(
        reason.startswith("positive_expectancy_rate_below_minimum:")
        or reason.startswith("p05_expectancy_not_positive:")
        for reason in result.reasons
    )



def test_independent_returns_collapse_same_hour_predictions() -> None:
    created_at = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    predictions = tuple(
        Prediction(
            id=uuid4(),
            symbol="BTCUSDT",
            created_at=created_at + timedelta(minutes=minute),
            expires_at=created_at + timedelta(minutes=minute + 15),
            horizon_seconds=900,
            direction=PredictionDirection.LONG,
            entry_price=Decimal("100"),
            raw_score=Decimal("0.30"),
            data_quality=Decimal("0.90"),
            model_name="test",
        )
        for minute in (0, 1, 2)
    )
    result = ReplayResult(
        decisions=(),
        predictions=predictions,
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=created_at + timedelta(minutes=minute, seconds=30),
            price=Decimal("100.50"),
        )
        for minute in (0, 1, 2)
    ]

    independent = build_candidate_independent_returns(
        result,
        points,
        take_profit_pct=Decimal("0.40"),
        stop_loss_pct=Decimal("0.18"),
        round_trip_cost_pct=Decimal("0.12"),
        bucket_minutes=60,
    )

    assert independent == (Decimal("0.28"),)
