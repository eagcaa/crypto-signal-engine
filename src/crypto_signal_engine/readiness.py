from dataclasses import dataclass

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
    required_horizons: tuple[int, ...] = (300, 900),
    required_directions: tuple[str, ...] = ("long", "short"),
) -> ReadinessResult:
    """Evaluate explicit gates before a live-money pilot is even considered."""

    reasons: list[str] = []

    ready_keys = {
        (bucket.horizon_seconds, bucket.direction)
        for bucket in calibration_buckets
        if bucket.calibrated_confidence is not None
    }

    for horizon in required_horizons:
        for direction in required_directions:
            if (horizon, direction) not in ready_keys:
                reasons.append(
                    "calibration_not_ready:"
                    f"{horizon}s:{direction}"
                )

    if not paper_validation.passed:
        reasons.extend(
            f"paper:{reason}"
            for reason in paper_validation.reasons
        )

    return ReadinessResult(
        ready=not reasons,
        reasons=tuple(reasons),
    )
