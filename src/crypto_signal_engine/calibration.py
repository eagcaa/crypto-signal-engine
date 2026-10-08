from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.predictions.models import Prediction
from crypto_signal_engine.replay.report import ReplayReport, ScoreBinStats


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    horizon_seconds: int
    lower_bound: Decimal
    upper_bound: Decimal | None
    samples: int
    observed_tp_rate: Decimal | None
    calibrated_confidence: Decimal | None

    @property
    def is_ready(self) -> bool:
        return self.calibrated_confidence is not None


class ReplayCalibrator:
    """Convert replay score bins into conservative confidence candidates.

    A confidence value is exposed only after a minimum number of evaluated
    samples exists for the bucket. Until then, the caller gets None rather
    than an invented percentage.
    """

    def __init__(self, *, minimum_samples: int = 30) -> None:
        if minimum_samples <= 0:
            raise ValueError("minimum_samples must be positive")
        self._minimum_samples = minimum_samples

    def build(
        self,
        report: ReplayReport,
    ) -> tuple[CalibrationBucket, ...]:
        return tuple(
            self._bucket(item)
            for item in report.by_score_bin
        )

    def confidence_for_prediction(
        self,
        prediction: Prediction,
        buckets: tuple[CalibrationBucket, ...],
    ) -> Decimal | None:
        absolute_score = abs(prediction.raw_score)

        for bucket in buckets:
            if bucket.horizon_seconds != prediction.horizon_seconds:
                continue
            if absolute_score < bucket.lower_bound:
                continue
            if (
                bucket.upper_bound is not None
                and absolute_score >= bucket.upper_bound
            ):
                continue
            return bucket.calibrated_confidence

        return None

    def _bucket(
        self,
        item: ScoreBinStats,
    ) -> CalibrationBucket:
        evaluated = item.stats.evaluated
        observed_tp_rate = item.stats.tp_rate

        calibrated_confidence = None
        if (
            evaluated >= self._minimum_samples
            and observed_tp_rate is not None
        ):
            calibrated_confidence = observed_tp_rate

        return CalibrationBucket(
            horizon_seconds=item.horizon_seconds,
            lower_bound=item.lower_bound,
            upper_bound=item.upper_bound,
            samples=evaluated,
            observed_tp_rate=observed_tp_rate,
            calibrated_confidence=calibrated_confidence,
        )
