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



def _regime_switching_series(days: int, seed: int = 11) -> list[PricePoint]:
    """Random walk whose volatility switches between calm and volatile days."""
    import random

    rng = random.Random(seed)
    price = 100.0
    points = []
    for minute in range(days * 1440):
        day = minute // 1440
        sigma = 0.0003 if day % 4 < 2 else 0.0015
        price *= exp(rng.gauss(0.0, sigma))
        points.append(
            PricePoint(timestamp=T0 + timedelta(minutes=minute), price=price)
        )
    return points


def test_plain_band_hides_regime_miscalibration_scaled_band_fixes_it() -> None:
    points = _regime_switching_series(40)

    plain = evaluate_walk_forward_range(
        points,
        horizon_minutes=60,
        target_coverage=0.70,
        lookback_days=10,
        minimum_calibration_count=20,
    )
    scaled = evaluate_walk_forward_range(
        points,
        horizon_minutes=60,
        target_coverage=0.70,
        lookback_days=10,
        minimum_calibration_count=20,
        volatility_scaled=True,
    )

    # Plain band: far too wide when calm, far too narrow when volatile.
    assert plain.coverage_low_vol > 0.95
    assert plain.coverage_high_vol < 0.50
    # Volatility-scaled band stays near target in every regime.
    for value in (
        scaled.coverage_low_vol,
        scaled.coverage_mid_vol,
        scaled.coverage_high_vol,
    ):
        assert 0.55 < value < 0.85
    assert abs(scaled.empirical_coverage - 0.70) < 0.08
