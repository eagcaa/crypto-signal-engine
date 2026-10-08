import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.predictions.models import Prediction
from crypto_signal_engine.replay.report import ReplayReport, ScoreBinStats


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    horizon_seconds: int
    direction: str
    model_name: str
    lower_bound: Decimal
    upper_bound: Decimal | None
    samples: int
    observed_tp_rate: Decimal | None
    calibrated_confidence: Decimal | None

    @property
    def is_ready(self) -> bool:
        return self.calibrated_confidence is not None


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    schema_version: int
    generated_at: datetime
    symbol: str
    start: datetime
    end: datetime
    price_source: str
    minimum_samples: int
    buckets: tuple[CalibrationBucket, ...]


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
            if bucket.model_name != prediction.model_name:
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
            model_name=item.model_name,
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

    payload = [_bucket_to_payload(bucket) for bucket in buckets]

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

    if isinstance(raw, dict):
        buckets_raw = raw.get("buckets")
        if not isinstance(buckets_raw, list):
            raise ValueError("Calibration artifact buckets must be a list")
        return tuple(_bucket_from_payload(item) for item in buckets_raw)

    if not isinstance(raw, list):
        raise ValueError("Calibration file must contain a list or artifact")

    return tuple(_bucket_from_payload(item) for item in raw)


def save_calibration_artifact(
    path: str | Path,
    artifact: CalibrationArtifact,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": artifact.schema_version,
        "generated_at": artifact.generated_at.isoformat(),
        "symbol": artifact.symbol,
        "start": artifact.start.isoformat(),
        "end": artifact.end.isoformat(),
        "price_source": artifact.price_source,
        "minimum_samples": artifact.minimum_samples,
        "buckets": [_bucket_to_payload(bucket) for bucket in artifact.buckets],
    }

    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_calibration_artifact(
    path: str | Path,
) -> CalibrationArtifact | None:
    source = Path(path)
    if not source.exists():
        return None

    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None

    buckets_raw = raw.get("buckets")
    if not isinstance(buckets_raw, list):
        raise ValueError("Calibration artifact buckets must be a list")

    return CalibrationArtifact(
        schema_version=int(raw.get("schema_version", 1)),
        generated_at=datetime.fromisoformat(str(raw["generated_at"])),
        symbol=str(raw["symbol"]),
        start=datetime.fromisoformat(str(raw["start"])),
        end=datetime.fromisoformat(str(raw["end"])),
        price_source=str(raw["price_source"]),
        minimum_samples=int(raw["minimum_samples"]),
        buckets=tuple(_bucket_from_payload(item) for item in buckets_raw),
    )


def _bucket_to_payload(bucket: CalibrationBucket) -> dict[str, object]:
    return {
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


def _bucket_from_payload(item: object) -> CalibrationBucket:
    if not isinstance(item, dict):
        raise ValueError("Calibration bucket must be an object")

    upper_raw = item.get("upper_bound")
    observed_raw = item.get("observed_tp_rate")
    confidence_raw = item.get("calibrated_confidence")

    return CalibrationBucket(
        horizon_seconds=int(item["horizon_seconds"]),
        direction=str(item["direction"]),
        model_name=str(item["model_name"]),
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



def calibration_artifact_rejection_reason(
    artifact: CalibrationArtifact | None,
    *,
    now: datetime,
    maximum_age: timedelta,
    symbol: str,
    required_price_source: str = "binance_spot_aggTrades",
) -> str | None:
    if artifact is None:
        return "missing"

    if artifact.price_source != required_price_source:
        return f"price_source:{artifact.price_source}"

    if artifact.symbol.upper() != symbol.upper():
        return f"symbol:{artifact.symbol}"

    age = now - artifact.generated_at
    if age > maximum_age:
        return f"stale:{int(age.total_seconds())}s"

    return None
