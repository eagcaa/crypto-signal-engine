from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from types import SimpleNamespace

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    CandidateGate,
    ReplayResult,
    filter_replay_result,
    prediction_passes_gate,
)


def _prediction(
    *,
    direction: PredictionDirection,
    horizon_seconds: int,
    contributions: dict[str, Decimal],
) -> Prediction:
    created_at = datetime(2026, 10, 9, 7, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=horizon_seconds),
        horizon_seconds=horizon_seconds,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("-0.30") if direction == PredictionDirection.SHORT else Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="test",
        feature_contributions=contributions,
    )


def test_candidate_gate_requires_support_count_and_oi_alignment() -> None:
    prediction = _prediction(
        direction=PredictionDirection.SHORT,
        horizon_seconds=900,
        contributions={
            "trend": Decimal("-0.10"),
            "futures_cvd": Decimal("-0.10"),
            "spot_cvd": Decimal("-0.05"),
            "open_interest": Decimal("-0.08"),
            "liquidations": Decimal("-0.03"),
            "crowding": Decimal("-0.02"),
            "order_book": Decimal("0.02"),
        },
    )
    gate = CandidateGate(
        name="test",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
    )

    assert prediction_passes_gate(prediction, gate) is True


def test_candidate_gate_rejects_oi_opposition() -> None:
    prediction = _prediction(
        direction=PredictionDirection.SHORT,
        horizon_seconds=900,
        contributions={
            "trend": Decimal("-0.10"),
            "futures_cvd": Decimal("-0.10"),
            "spot_cvd": Decimal("-0.05"),
            "open_interest": Decimal("0.08"),
            "liquidations": Decimal("-0.03"),
            "crowding": Decimal("-0.02"),
            "taker_flow": Decimal("-0.01"),
        },
    )
    gate = CandidateGate(
        name="test",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
    )

    assert prediction_passes_gate(prediction, gate) is False


def test_filter_replay_result_keeps_only_accepted_predictions() -> None:
    accepted = _prediction(
        direction=PredictionDirection.LONG,
        horizon_seconds=300,
        contributions={
            "a": Decimal("0.1"),
            "b": Decimal("0.1"),
            "c": Decimal("0.1"),
            "d": Decimal("0.1"),
            "e": Decimal("0.1"),
            "f": Decimal("0.1"),
        },
    )
    rejected = _prediction(
        direction=PredictionDirection.LONG,
        horizon_seconds=300,
        contributions={
            "a": Decimal("0.1"),
            "b": Decimal("0.1"),
        },
    )
    result = ReplayResult(
        decisions=(),
        predictions=(accepted, rejected),
        evaluations=(),
        open_predictions=(),
    )
    gate = CandidateGate(
        name="test",
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        minimum_supporting_features=6,
    )

    filtered = filter_replay_result(result, gate)

    assert filtered.predictions == (accepted,)



def test_research_candidate_has_frozen_holdout_barriers() -> None:
    from crypto_signal_engine.replay import CANDIDATE_GATES

    gate = next(
        item
        for item in CANDIDATE_GATES
        if item.name == "15m_short_support6_oi"
    )

    assert gate.frozen_take_profit_pct == Decimal("0.15")
    assert gate.frozen_stop_loss_pct == Decimal("0.18")



def test_wide_candidate_freezes_second_holdout_barriers() -> None:
    from crypto_signal_engine.replay import CANDIDATE_GATES

    gate = next(
        item
        for item in CANDIDATE_GATES
        if item.name == "15m_short_support6_oi_wide"
    )

    assert gate.frozen_take_profit_pct == Decimal("0.40")
    assert gate.frozen_stop_loss_pct == Decimal("0.30")



def test_regime_gate_requires_matching_15m_regime() -> None:
    prediction = _prediction(
        direction=PredictionDirection.SHORT,
        horizon_seconds=900,
        contributions={
            "trend": Decimal("-0.10"),
            "futures_cvd": Decimal("-0.10"),
            "spot_cvd": Decimal("-0.05"),
            "open_interest": Decimal("-0.08"),
            "liquidations": Decimal("-0.03"),
            "crowding": Decimal("-0.02"),
        },
    )
    gate = CandidateGate(
        name="test_regime",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        required_trend_regime="downtrend",
        required_volatility_regime="high",
    )

    matching = SimpleNamespace(
        trend_regime_15m="downtrend",
        volatility_regime_15m="high",
    )
    wrong = SimpleNamespace(
        trend_regime_15m="uptrend",
        volatility_regime_15m="high",
    )

    assert prediction_passes_gate(prediction, gate, matching) is True
    assert prediction_passes_gate(prediction, gate, wrong) is False
    assert prediction_passes_gate(prediction, gate, None) is False


def test_regime_candidate_freezes_wide_barriers() -> None:
    from crypto_signal_engine.replay import CANDIDATE_GATES

    gate = next(
        item
        for item in CANDIDATE_GATES
        if item.name == "15m_short_support6_oi_wide_downtrend_high"
    )

    assert gate.required_trend_regime == "downtrend"
    assert gate.required_volatility_regime == "high"
    assert gate.frozen_take_profit_pct == Decimal("0.40")
    assert gate.frozen_stop_loss_pct == Decimal("0.30")



def test_long_range_high_candidates_are_research_only() -> None:
    from crypto_signal_engine.replay import CANDIDATE_GATES

    by_name = {gate.name: gate for gate in CANDIDATE_GATES}

    regime_only = by_name["15m_long_range_high"]
    support6 = by_name["15m_long_support6_range_high"]
    support6_oi = by_name["15m_long_support6_oi_range_high"]

    for gate in (regime_only, support6, support6_oi):
        assert gate.horizon_seconds == 900
        assert gate.direction == PredictionDirection.LONG
        assert gate.required_trend_regime == "range"
        assert gate.required_volatility_regime == "high"
        assert gate.frozen_take_profit_pct is None
        assert gate.frozen_stop_loss_pct is None

    assert regime_only.minimum_supporting_features == 0
    assert support6.minimum_supporting_features == 6
    assert support6_oi.minimum_supporting_features == 6
    assert support6_oi.require_open_interest_support is True



def test_long_range_high_candidate_freezes_barriers() -> None:
    from crypto_signal_engine.replay import CANDIDATE_GATES

    gate = next(
        item
        for item in CANDIDATE_GATES
        if item.name == "15m_long_range_high"
    )

    assert gate.frozen_take_profit_pct == Decimal("0.40")
    assert gate.frozen_stop_loss_pct == Decimal("0.18")
