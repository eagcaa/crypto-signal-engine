from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.predictions.models import Prediction, PredictionDirection
from crypto_signal_engine.replay.models import ReplayResult


@dataclass(frozen=True, slots=True)
class CandidateGate:
    name: str
    horizon_seconds: int
    direction: PredictionDirection
    minimum_supporting_features: int = 0
    require_open_interest_support: bool = False
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
        name="15m_short_support6_oi",
        horizon_seconds=900,
        direction=PredictionDirection.SHORT,
        minimum_supporting_features=6,
        require_open_interest_support=True,
        frozen_take_profit_pct=Decimal("0.15"),
        frozen_stop_loss_pct=Decimal("0.18"),
    ),
)


def filter_replay_result(
    result: ReplayResult,
    gate: CandidateGate,
) -> ReplayResult:
    accepted = tuple(
        prediction
        for prediction in result.predictions
        if prediction_passes_gate(prediction, gate)
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

    return True
