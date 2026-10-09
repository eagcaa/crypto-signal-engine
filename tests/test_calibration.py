from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.calibration import (
    CalibrationArtifact,
    ReplayCalibrator,
    calibration_artifact_rejection_reason,
    load_calibration,
    load_calibration_artifact,
    save_calibration,
    save_calibration_artifact,
)
from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay.report import ReplayReport, ReplayStats, ScoreBinStats


def make_report(*, evaluated: int, take_profit: int) -> ReplayReport:
    stats = ReplayStats(
        predictions=evaluated,
        take_profit=take_profit,
        stop_loss=evaluated - take_profit,
        expired_no_touch=0,
        expired_without_data=0,
    )
    return ReplayReport(
        by_horizon={300: stats},
        by_regime=(),
        by_score_bin=(
            ScoreBinStats(
                horizon_seconds=300,
                direction="long",
                model_name="composite_rules_v4_5m",
                lower_bound=Decimal("0.20"),
                upper_bound=Decimal("0.25"),
                stats=stats,
            ),
        ),
    )


def make_prediction(score: str = "0.22") -> Prediction:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        entry_price=Decimal("100"),
        raw_score=Decimal(score),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
    )


def test_calibration_refuses_small_samples() -> None:
    calibrator = ReplayCalibrator(minimum_samples=30)
    buckets = calibrator.build(
        make_report(evaluated=20, take_profit=14)
    )

    assert buckets[0].samples == 20
    assert buckets[0].observed_tp_rate == Decimal("70")
    assert buckets[0].calibrated_confidence is None
    assert calibrator.confidence_for_prediction(
        make_prediction(),
        buckets,
    ) is None


def test_calibration_exposes_observed_rate_after_minimum_samples() -> None:
    calibrator = ReplayCalibrator(minimum_samples=30)
    buckets = calibrator.build(
        make_report(evaluated=40, take_profit=28)
    )

    assert buckets[0].samples == 40
    assert buckets[0].calibrated_confidence == Decimal("70")
    assert calibrator.confidence_for_prediction(
        make_prediction(),
        buckets,
    ) == Decimal("70")


def test_calibration_does_not_cross_horizon_or_score_bucket() -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=6)
    )

    assert calibrator.confidence_for_prediction(
        make_prediction("0.31"),
        buckets,
    ) is None



def test_calibration_does_not_cross_direction() -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=6)
    )
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    short_prediction = Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.SHORT,
        entry_price=Decimal("100"),
        raw_score=Decimal("-0.22"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
    )

    assert calibrator.confidence_for_prediction(
        short_prediction,
        buckets,
    ) is None



def test_calibration_round_trip(tmp_path) -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=7)
    )
    target = tmp_path / "calibration.json"

    save_calibration(target, buckets)
    restored = load_calibration(target)

    assert restored == buckets


def test_load_calibration_returns_empty_when_missing(tmp_path) -> None:
    assert load_calibration(tmp_path / "missing.json") == ()



def test_calibration_does_not_cross_model_version() -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=6)
    )
    prediction = make_prediction()
    stale_model_prediction = Prediction(
        id=prediction.id,
        symbol=prediction.symbol,
        created_at=prediction.created_at,
        expires_at=prediction.expires_at,
        horizon_seconds=prediction.horizon_seconds,
        direction=prediction.direction,
        entry_price=prediction.entry_price,
        raw_score=prediction.raw_score,
        data_quality=prediction.data_quality,
        model_name="composite_rules_v3_5m",
        feature_contributions=prediction.feature_contributions,
        reason=prediction.reason,
    )

    assert calibrator.confidence_for_prediction(
        stale_model_prediction,
        buckets,
    ) is None



def test_calibration_artifact_round_trip(tmp_path) -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=7)
    )
    generated_at = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    artifact = CalibrationArtifact(
        schema_version=1,
        generated_at=generated_at,
        symbol="BTCUSDT",
        start=generated_at - timedelta(hours=6),
        end=generated_at,
        price_source="binance_spot_aggTrades",
        minimum_samples=30,
        buckets=buckets,
    )
    target = tmp_path / "calibration.json"

    save_calibration_artifact(target, artifact)

    restored_artifact = load_calibration_artifact(target)
    restored_buckets = load_calibration(target)

    assert restored_artifact == artifact
    assert restored_buckets == buckets


def test_legacy_calibration_file_has_no_artifact_metadata(tmp_path) -> None:
    calibrator = ReplayCalibrator(minimum_samples=1)
    buckets = calibrator.build(
        make_report(evaluated=10, take_profit=7)
    )
    target = tmp_path / "legacy.json"

    save_calibration(target, buckets)

    assert load_calibration_artifact(target) is None
    assert load_calibration(target) == buckets



def test_calibration_artifact_validation_checks_source_symbol_and_age() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    artifact = CalibrationArtifact(
        schema_version=1,
        generated_at=now - timedelta(hours=1),
        symbol="BTCUSDT",
        start=now - timedelta(hours=7),
        end=now - timedelta(hours=1),
        price_source="binance_spot_aggTrades",
        minimum_samples=30,
        buckets=(),
    )

    assert calibration_artifact_rejection_reason(
        artifact,
        now=now,
        maximum_age=timedelta(days=7),
        symbol="BTCUSDT",
    ) is None

    assert calibration_artifact_rejection_reason(
        artifact,
        now=now,
        maximum_age=timedelta(minutes=30),
        symbol="BTCUSDT",
    ).startswith("stale:")

    assert calibration_artifact_rejection_reason(
        artifact,
        now=now,
        maximum_age=timedelta(days=7),
        symbol="ETHUSDT",
    ) == "symbol:BTCUSDT"
