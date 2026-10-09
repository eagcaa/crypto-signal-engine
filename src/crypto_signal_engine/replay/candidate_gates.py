from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.predictions.models import Prediction, PredictionDirection
from crypto_signal_engine.replay.models import ReplayResult


@dataclass(frozen=True, slots=True)
class CandidateGate:
    name: str
    horizon_seconds: int
    direction: PredictionDirection
    minimum_supporting_features: int = 0
    require_open_interest_support: bool = False
    required_trend_regime: str | None = None
    required_volatility_regime: str | None = None
    frozen_take_profit_pct: Decimal | None = None
    frozen_stop_loss_pct: Decimal | None = None


CANDIDATE_GATES = (
    CandidateGate(
        name="5m_long_support6",
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        minimum_supporting_features=6,
    ),
    CandidateGate(
        name="15m_short_support6",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
    ),
    CandidateGate(
        name="15m_long_range_high",
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        required_trend_regime="range",
        required_volatility_regime="high",
    ),
    CandidateGate(
        name="15m_long_support6_range_high",
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        minimum_supporting_features=6,
        required_trend_regime="range",
        required_volatility_regime="high",
    ),
    CandidateGate(
        name="15m_long_support6_oi_range_high",
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        required_trend_regime="range",
        required_volatility_regime="high",
    ),
    CandidateGate(
        name="15m_short_support6_oi",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        frozen_take_profit_pct=Decimal("0.15"),
        frozen_stop_loss_pct=Decimal("0.18"),
    ),
    CandidateGate(
        name="15m_short_support6_oi_wide",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        frozen_take_profit_pct=Decimal("0.40"),
        frozen_stop_loss_pct=Decimal("0.30"),
    ),
    CandidateGate(
        name="15m_short_support6_oi_wide_downtrend_high",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        required_trend_regime="downtrend",
        required_volatility_regime="high",
        frozen_take_profit_pct=Decimal("0.40"),
        frozen_stop_loss_pct=Decimal("0.30"),
    ),
)


def filter_replay_result(
    result: ReplayResult,
    gate: CandidateGate,
    feature_snapshots: list[ResearchFeatureSnapshot] | None = None,
) -> ReplayResult:
    feature_by_key = {
        (feature.symbol.upper(), feature.timestamp): feature
        for feature in (feature_snapshots or [])
    }
    accepted = tuple(
        prediction
        for prediction in result.predictions
        if prediction_passes_gate(
            prediction,
            gate,
            feature_by_key.get(
                (prediction.symbol.upper(), prediction.created_at)
            ),
        )
    )
    accepted_ids = {prediction.id for prediction in accepted}

    return ReplayResult(
        decisions=result.decisions,
        predictions=accepted,
        evaluations=tuple(
            evaluation
            for evaluation in result.evaluations
            if evaluation.prediction_id in accepted_ids
        ),
        open_predictions=tuple(
            prediction
            for prediction in result.open_predictions
            if prediction.id in accepted_ids
        ),
    )


def prediction_passes_gate(
    prediction: Prediction,
    gate: CandidateGate,
    feature_snapshot: ResearchFeatureSnapshot | None = None,
) -> bool:
    if prediction.horizon_seconds != gate.horizon_seconds:
        return False
    if prediction.direction != gate.direction:
        return False

    contributions = prediction.feature_contributions or {}
    direction_sign = (
        Decimal("1")
        if prediction.direction == PredictionDirection.LONG
        else Decimal("-1")
    )
    supporting_features = sum(
        1
        for contribution in contributions.values()
        if contribution * direction_sign > 0
    )
    if supporting_features < gate.minimum_supporting_features:
        return False

    if gate.require_open_interest_support:
        open_interest = contributions.get("open_interest")
        if open_interest is None or open_interest * direction_sign <= 0:
            return False

    if (
        gate.required_trend_regime is not None
        or gate.required_volatility_regime is not None
    ):
        if feature_snapshot is None:
            return False
        if prediction.horizon_seconds == 900:
            trend_regime = feature_snapshot.trend_regime_15m
            volatility_regime = feature_snapshot.volatility_regime_15m
        else:
            trend_regime = feature_snapshot.trend_regime_5m
            volatility_regime = feature_snapshot.volatility_regime_5m

        if (
            gate.required_trend_regime is not None
            and trend_regime != gate.required_trend_regime
        ):
            return False
        if (
            gate.required_volatility_regime is not None
            and volatility_regime != gate.required_volatility_regime
        ):
            return False

    return True
