from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.predictions.models import PredictionEvaluationOutcome
from crypto_signal_engine.replay.models import ReplayResult


@dataclass(frozen=True, slots=True)
class ReplayStats:
    predictions: int
    take_profit: int
    stop_loss: int
    expired_no_touch: int
    expired_without_data: int

    @property
    def evaluated(self) -> int:
        return self.take_profit + self.stop_loss + self.expired_no_touch

    @property
    def tp_rate(self) -> Decimal | None:
        if self.evaluated == 0:
            return None
        return (
            Decimal(self.take_profit)
            / Decimal(self.evaluated)
            * Decimal("100")
        )


@dataclass(frozen=True, slots=True)
class RegimeStats:
    horizon_seconds: int
    direction: str
    trend_regime: str
    volatility_regime: str
    stats: ReplayStats


@dataclass(frozen=True, slots=True)
class ReplayReport:
    by_horizon: dict[int, ReplayStats]
    by_regime: tuple[RegimeStats, ...]


def build_replay_report(
    result: ReplayResult,
    feature_snapshots: list[ResearchFeatureSnapshot],
) -> ReplayReport:
    evaluation_by_prediction = {
        evaluation.prediction_id: evaluation
        for evaluation in result.evaluations
    }
    feature_by_key = {
        (feature.symbol.upper(), feature.timestamp): feature
        for feature in feature_snapshots
    }

    horizon_groups: dict[int, list] = defaultdict(list)
    regime_groups: dict[tuple[int, str, str, str], list] = defaultdict(list)

    for prediction in result.predictions:
        evaluation = evaluation_by_prediction.get(prediction.id)
        horizon_groups[prediction.horizon_seconds].append(evaluation)

        feature = feature_by_key.get(
            (prediction.symbol.upper(), prediction.created_at)
        )
        if feature is None:
            continue

        if prediction.horizon_seconds == 900:
            trend_regime = feature.trend_regime_15m or "unknown"
            volatility_regime = feature.volatility_regime_15m or "unknown"
        else:
            trend_regime = feature.trend_regime_5m or "unknown"
            volatility_regime = feature.volatility_regime_5m or "unknown"

        regime_groups[
            (
                prediction.horizon_seconds,
                prediction.direction.value,
                trend_regime,
                volatility_regime,
            )
        ].append(evaluation)

    by_horizon = {
        horizon: _stats(evaluations)
        for horizon, evaluations in sorted(horizon_groups.items())
    }

    by_regime = tuple(
        RegimeStats(
            horizon_seconds=key[0],
            direction=key[1],
            trend_regime=key[2],
            volatility_regime=key[3],
            stats=_stats(evaluations),
        )
        for key, evaluations in sorted(regime_groups.items())
    )

    return ReplayReport(
        by_horizon=by_horizon,
        by_regime=by_regime,
    )


def _stats(evaluations: list) -> ReplayStats:
    take_profit = 0
    stop_loss = 0
    expired_no_touch = 0
    expired_without_data = 0

    for evaluation in evaluations:
        if evaluation is None:
            continue
        if evaluation.outcome == PredictionEvaluationOutcome.TAKE_PROFIT:
            take_profit += 1
        elif evaluation.outcome == PredictionEvaluationOutcome.STOP_LOSS:
            stop_loss += 1
        elif evaluation.outcome == PredictionEvaluationOutcome.EXPIRED_NO_TOUCH:
            expired_no_touch += 1
        elif (
            evaluation.outcome
            == PredictionEvaluationOutcome.EXPIRED_WITHOUT_DATA
        ):
            expired_without_data += 1

    return ReplayStats(
        predictions=len(evaluations),
        take_profit=take_profit,
        stop_loss=stop_loss,
        expired_no_touch=expired_no_touch,
        expired_without_data=expired_without_data,
    )
