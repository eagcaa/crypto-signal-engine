from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from crypto_signal_engine.paper import CandidatePaperTracker, PaperBroker
from crypto_signal_engine.predictions import Prediction, PredictionDirection


def _prediction(
    *,
    direction: PredictionDirection = PredictionDirection.LONG,
    horizon_seconds: int = 900,
) -> Prediction:
    created_at = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=horizon_seconds),
        horizon_seconds=horizon_seconds,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.90"),
        take_profit_pct=Decimal("0.55"),
        stop_loss_pct=Decimal("0.35"),
        model_name="composite_rules_v4_15m",
        feature_contributions={"trend": Decimal("0.10")},
        reason="base",
    )


def test_candidate_tracker_clones_matching_prediction_with_frozen_barriers() -> None:
    tracker = CandidatePaperTracker(PaperBroker())
    features = SimpleNamespace(
        trend_regime_15m="range",
        volatility_regime_15m="high",
    )
    base = _prediction()

    candidate = tracker.candidate_prediction(base, features)

    assert candidate is not None
    assert candidate.id != base.id
    assert candidate.direction == PredictionDirection.LONG
    assert candidate.horizon_seconds == 900
    assert candidate.take_profit_pct == Decimal("0.40")
    assert candidate.stop_loss_pct == Decimal("0.18")
    assert candidate.model_name == (
        "candidate_paper_v5_15m_long_range_high"
    )
    assert "forward_candidate=15m_long_range_high" in (candidate.reason or "")


def test_candidate_tracker_rejects_wrong_regime_or_direction() -> None:
    tracker = CandidatePaperTracker(PaperBroker())
    wrong_regime = SimpleNamespace(
        trend_regime_15m="uptrend",
        volatility_regime_15m="high",
    )
    matching = SimpleNamespace(
        trend_regime_15m="range",
        volatility_regime_15m="high",
    )

    assert tracker.candidate_prediction(_prediction(), wrong_regime) is None
    assert (
        tracker.candidate_prediction(
            _prediction(direction=PredictionDirection.SHORT),
            matching,
        )
        is None
    )


def test_candidate_tracker_requires_frozen_gate() -> None:
    try:
        CandidatePaperTracker(
            PaperBroker(),
            candidate_name="15m_long_support6_range_high",
        )
    except ValueError as exc:
        assert "not frozen" in str(exc)
    else:
        raise AssertionError("Expected unfrozen candidate to be rejected")
