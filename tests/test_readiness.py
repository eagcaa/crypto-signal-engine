from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import CalibrationBucket
from crypto_signal_engine.paper.validation import PaperValidationResult
from crypto_signal_engine.readiness import evaluate_readiness


def bucket(horizon: int, direction: str) -> CalibrationBucket:
    return CalibrationBucket(
        horizon_seconds=horizon,
        direction=direction,
        lower_bound=Decimal("0.30"),
        upper_bound=Decimal("0.40"),
        samples=40,
        observed_tp_rate=Decimal("65"),
        calibrated_confidence=Decimal("65"),
    )


def test_readiness_passes_when_all_calibration_and_paper_gates_pass() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    result = evaluate_readiness(
        (
            bucket(300, "long"),
            bucket(300, "short"),
            bucket(900, "long"),
            bucket(900, "short"),
        ),
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now - timedelta(seconds=30),
        latest_market_quality=Decimal("0.83"),
        now=now,
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
    )

    assert result.ready is False
    assert "calibration_not_ready:300s:short" in result.reasons
    assert "calibration_not_ready:900s:long" in result.reasons
    assert "calibration_not_ready:900s:short" in result.reasons
    assert "paper:insufficient_trades:12/30" in result.reasons



def test_readiness_reports_stale_or_low_quality_market_data() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    result = evaluate_readiness(
        (
            bucket(300, "long"),
            bucket(300, "short"),
            bucket(900, "long"),
            bucket(900, "short"),
        ),
        PaperValidationResult(passed=True, reasons=()),
        latest_market_timestamp=now - timedelta(minutes=3),
        latest_market_quality=Decimal("0.50"),
        now=now,
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
