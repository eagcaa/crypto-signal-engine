from datetime import UTC, datetime, timedelta
from math import exp

from crypto_signal_engine.forecast.range import PricePoint, evaluate_walk_forward_range


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _series(minutes: int, step: float) -> list[PricePoint]:
    return [
        PricePoint(
            timestamp=T0 + timedelta(minutes=i),
            price=100.0 * exp(step * i),
        )
        for i in range(minutes + 1)
    ]


def test_walk_forward_range_reaches_expected_coverage_on_stable_path() -> None:
    points = _series(60 * 20, 0.0001)
    result = evaluate_walk_forward_range(
        points,
        horizon_minutes=60,
        target_coverage=0.70,
        lookback_days=10,
        minimum_calibration_count=5,
    )
    assert result.evaluated > 5
    assert result.empirical_coverage == 1.0
    assert result.average_full_width_pct > 0


def test_walk_forward_range_needs_calibration_history() -> None:
    points = _series(60 * 4, 0.0001)
    result = evaluate_walk_forward_range(
        points,
        horizon_minutes=60,
        target_coverage=0.70,
        minimum_calibration_count=20,
    )
    assert result.evaluated == 0
