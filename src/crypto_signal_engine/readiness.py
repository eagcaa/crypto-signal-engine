from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import CalibrationBucket
from crypto_signal_engine.paper.validation import PaperValidationResult


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
    required_models: tuple[tuple[int, str], ...] = (
        (300, "composite_rules_v3_5m"),
        (900, "composite_rules_v3_15m"),
    ),
    required_directions: tuple[str, ...] = ("long", "short"),
) -> ReadinessResult:
    """Evaluate explicit gates before a live-money pilot is even considered."""

    reasons: list[str] = []

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
