import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.predictions.models import Prediction
from crypto_signal_engine.replay.report import ReplayReport, ScoreBinStats


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    horizon_seconds: int
    direction: str
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
            if bucket.direction != prediction.direction.value:
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
            direction=item.direction,
            lower_bound=item.lower_bound,
            upper_bound=item.upper_bound,
            samples=evaluated,
            observed_tp_rate=observed_tp_rate,
            calibrated_confidence=calibrated_confidence,
        )



def save_calibration(
    path: str | Path,
    buckets: tuple[CalibrationBucket, ...],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = [
        {
            **asdict(bucket),
            "lower_bound": str(bucket.lower_bound),
            "upper_bound": (
                str(bucket.upper_bound)
                if bucket.upper_bound is not None
                else None
            ),
            "observed_tp_rate": (
                str(bucket.observed_tp_rate)
                if bucket.observed_tp_rate is not None
                else None
            ),
            "calibrated_confidence": (
                str(bucket.calibrated_confidence)
                if bucket.calibrated_confidence is not None
                else None
            ),
        }
        for bucket in buckets
    ]

    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_calibration(
    path: str | Path,
) -> tuple[CalibrationBucket, ...]:
    source = Path(path)
    if not source.exists():
        return ()

    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Calibration file must contain a list")

    buckets: list[CalibrationBucket] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Calibration bucket must be an object")

        upper_raw = item.get("upper_bound")
        observed_raw = item.get("observed_tp_rate")
        confidence_raw = item.get("calibrated_confidence")

        buckets.append(
            CalibrationBucket(
                horizon_seconds=int(item["horizon_seconds"]),
                direction=str(item["direction"]),
                lower_bound=Decimal(str(item["lower_bound"])),
                upper_bound=(
                    Decimal(str(upper_raw))
                    if upper_raw is not None
                    else None
                ),
                samples=int(item["samples"]),
                observed_tp_rate=(
                    Decimal(str(observed_raw))
                    if observed_raw is not None
                    else None
                ),
                calibrated_confidence=(
                    Decimal(str(confidence_raw))
                    if confidence_raw is not None
                    else None
                ),
            )
        )

    return tuple(buckets)
