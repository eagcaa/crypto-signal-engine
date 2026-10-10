from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import CalibrationArtifact, CalibrationBucket
from crypto_signal_engine.paper.validation import PaperValidationResult
from crypto_signal_engine.readiness import evaluate_readiness


def artifact(
    now: datetime,
    buckets: tuple[CalibrationBucket, ...],
    *,
    price_source: str = "binance_spot_aggTrades",
    age: timedelta = timedelta(hours=1),
) -> CalibrationArtifact:
    return CalibrationArtifact(
        schema_version=1,
        generated_at=now - age,
        symbol="BTCUSDT",
        start=now - timedelta(hours=7),
        end=now - timedelta(hours=1),
        price_source=price_source,
        minimum_samples=30,
        buckets=buckets,
    )


def bucket(horizon: int, direction: str) -> CalibrationBucket:
    return CalibrationBucket(
        horizon_seconds=horizon,
        direction=direction,
        model_name=(
            "composite_rules_v4_5m"
            if horizon == 300
            else "composite_rules_v4_15m"
        ),
        lower_bound=Decimal("0.30"),
        upper_bound=Decimal("0.40"),
        samples=40,
        observed_tp_rate=Decimal("65"),
        calibrated_confidence=Decimal("65"),
    )


def test_readiness_passes_when_all_calibration_and_paper_gates_pass() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    buckets = (
            bucket(300, "long"),
            bucket(300, "short"),
            bucket(900, "long"),
            bucket(900, "short"),
            bucket(3600, "long"),
            bucket(3600, "short"),
        )
    result = evaluate_readiness(
        buckets,
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now - timedelta(seconds=30),
        latest_market_quality=Decimal("0.83"),
        now=now,
        calibration_artifact=artifact(now, buckets),
    )

    assert result.ready is True
    assert result.reasons == ()


def test_readiness_reports_missing_calibration_and_paper_failures() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    result = evaluate_readiness(
        (bucket(300, "long"),),
        PaperValidationResult(
            passed=False,
            reasons=("insufficient_trades:12/30",),
        ),
        latest_market_timestamp=now - timedelta(minutes=5),
        latest_market_quality=Decimal("0.50"),
        now=now,
        calibration_artifact=artifact(
            now,
            (bucket(300, "long"),),
        ),
    )

    assert result.ready is False
    assert (
        "calibration_not_ready:300s:short:composite_rules_v4_5m"
        in result.reasons
    )
    assert (
        "calibration_not_ready:900s:long:composite_rules_v4_15m"
        in result.reasons
    )
    assert (
        "calibration_not_ready:900s:short:composite_rules_v4_15m"
        in result.reasons
    )
    assert "paper:insufficient_trades:12/30" in result.reasons



def test_readiness_reports_stale_or_low_quality_market_data() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    buckets = (
            bucket(300, "long"),
            bucket(300, "short"),
            bucket(900, "long"),
            bucket(900, "short"),
        )
    result = evaluate_readiness(
        buckets,
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now - timedelta(minutes=3),
        latest_market_quality=Decimal("0.50"),
        now=now,
        calibration_artifact=artifact(now, buckets),
    )

    assert result.ready is False
    assert any(
        reason.startswith("market_snapshot_stale:")
        for reason in result.reasons
    )
    assert any(
        reason.startswith("market_quality_below_minimum:")
        for reason in result.reasons
    )



def test_readiness_rejects_stale_model_calibration() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    stale = CalibrationBucket(
        horizon_seconds=300,
        direction="long",
        model_name="composite_rules_v2_5m",
        lower_bound=Decimal("0.30"),
        upper_bound=Decimal("0.40"),
        samples=100,
        observed_tp_rate=Decimal("70"),
        calibrated_confidence=Decimal("70"),
    )

    result = evaluate_readiness(
        (
            stale,
            bucket(300, "short"),
            bucket(900, "long"),
            bucket(900, "short"),
        ),
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now,
        latest_market_quality=Decimal("0.83"),
        now=now,
        calibration_artifact=artifact(
            now,
            (
                stale,
                bucket(300, "short"),
                bucket(900, "long"),
                bucket(900, "short"),
            ),
        ),
    )

    assert result.ready is False
    assert (
        "calibration_not_ready:300s:long:composite_rules_v4_5m"
        in result.reasons
    )



def test_readiness_rejects_missing_or_stale_calibration_artifact() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    buckets = (
        bucket(300, "long"),
        bucket(300, "short"),
        bucket(900, "long"),
        bucket(900, "short"),
    )

    missing = evaluate_readiness(
        buckets,
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now,
        latest_market_quality=Decimal("0.83"),
        now=now,
    )
    stale = evaluate_readiness(
        buckets,
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now,
        latest_market_quality=Decimal("0.83"),
        now=now,
        calibration_artifact=artifact(
            now,
            buckets,
            age=timedelta(days=8),
        ),
    )

    assert "calibration_artifact_invalid:missing" in missing.reasons
    assert any(
        reason.startswith("calibration_artifact_invalid:stale:")
        for reason in stale.reasons
    )


def test_readiness_rejects_non_exact_calibration_source() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    buckets = (
        bucket(300, "long"),
        bucket(300, "short"),
        bucket(900, "long"),
        bucket(900, "short"),
    )

    result = evaluate_readiness(
        buckets,
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now,
        latest_market_quality=Decimal("0.83"),
        now=now,
        calibration_artifact=artifact(
            now,
            buckets,
            price_source="persisted_market_snapshots",
        ),
    )

    assert result.ready is False
    assert (
        "calibration_artifact_invalid:price_source:persisted_market_snapshots"
        in result.reasons
    )
