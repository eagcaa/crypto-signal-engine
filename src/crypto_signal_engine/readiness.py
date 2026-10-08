from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import CalibrationArtifact, CalibrationBucket
from crypto_signal_engine.paper.validation import PaperValidationResult
from crypto_signal_engine.predictions.engine import CompositePredictionEngine


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    reasons: tuple[str, ...]


def evaluate_readiness(
    calibration_buckets: tuple[CalibrationBucket, ...],
    paper_validation: PaperValidationResult,
    *,
    latest_market_timestamp: datetime | None = None,
    latest_market_quality: Decimal | None = None,
    now: datetime | None = None,
    maximum_market_age: timedelta = timedelta(minutes=2),
    minimum_market_quality: Decimal = Decimal("0.67"),
    calibration_artifact: CalibrationArtifact | None = None,
    maximum_calibration_age: timedelta = timedelta(days=7),
    required_models: tuple[tuple[int, str], ...] | None = None,
    required_directions: tuple[str, ...] = ("long", "short"),
) -> ReadinessResult:
    """Evaluate explicit gates before a live-money pilot is even considered."""

    reasons: list[str] = []
    required_models = (
        required_models
        if required_models is not None
        else CompositePredictionEngine.current_model_names()
    )

    ready_keys = {
        (
            bucket.horizon_seconds,
            bucket.direction,
            bucket.model_name,
        )
        for bucket in calibration_buckets
        if bucket.calibrated_confidence is not None
    }

    for horizon, model_name in required_models:
        for direction in required_directions:
            if (horizon, direction, model_name) not in ready_keys:
                reasons.append(
                    "calibration_not_ready:"
                    f"{horizon}s:{direction}:{model_name}"
                )

    if calibration_artifact is None:
        reasons.append("calibration_artifact_missing")
    else:
        if calibration_artifact.price_source != "binance_spot_aggTrades":
            reasons.append(
                "calibration_price_source_invalid:"
                f"{calibration_artifact.price_source}"
            )
        if now is not None:
            artifact_age = now - calibration_artifact.generated_at
            if artifact_age > maximum_calibration_age:
                reasons.append(
                    "calibration_artifact_stale:"
                    f"{int(artifact_age.total_seconds())}s"
                )

    if not paper_validation.passed:
        reasons.extend(
            f"paper:{reason}"
            for reason in paper_validation.reasons
        )

    if latest_market_timestamp is None:
        reasons.append("market_snapshot_missing")
    elif now is not None:
        market_age = now - latest_market_timestamp
        if market_age > maximum_market_age:
            reasons.append(
                "market_snapshot_stale:"
                f"{int(market_age.total_seconds())}s"
            )

    if latest_market_quality is None:
        reasons.append("market_quality_missing")
    elif latest_market_quality < minimum_market_quality:
        reasons.append(
            "market_quality_below_minimum:"
            f"{latest_market_quality:.2f}/{minimum_market_quality:.2f}"
        )

    return ReadinessResult(
        ready=not reasons,
        reasons=tuple(reasons),
    )
