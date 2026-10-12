"""Causal price-range calibration from realized future excursion.

The forecast is deliberately direction-agnostic.  For an anchor price P and
horizon H, it estimates a radius q from *past* maximum absolute log excursions
and returns [P * exp(-q), P * exp(q)].  A hit means the entire observed future
path through H stayed inside that band.

Two modes:

- plain: q is a quantile of past raw excursions (same width in calm and
  volatile markets).
- volatility-scaled: past excursions are divided by the trailing realized
  volatility at their anchor; q is that quantile times the *current*
  trailing volatility, so the band widens when the market is active.

Coverage is also reported per tercile of trailing volatility, because a band
that averages 70% can still be far too wide in calm markets and far too
narrow in volatile ones.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, exp, log, sqrt
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    price: float


@dataclass(frozen=True, slots=True)
class LiveRangeEstimate:
    horizon_minutes: int
    target_coverage: float
    lower: float
    upper: float
    radius_pct: float
    calibration_count: int
    volatility_scaled: bool


@dataclass(frozen=True, slots=True)
class RangeCoverageResult:
    horizon_minutes: int
    target_coverage: float
    evaluated: int
    covered: int
    empirical_coverage: float
    average_full_width_pct: float
    average_radius_pct: float
    minimum_calibration_count: int
    volatility_scaled: bool = False
    coverage_low_vol: float | None = None
    coverage_mid_vol: float | None = None
    coverage_high_vol: float | None = None


def _higher_quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0 < probability < 1:
        raise ValueError("probability must be between 0 and 1")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _future_excursion(
    points: Sequence[PricePoint],
    times: Sequence[datetime],
    anchor_index: int,
    horizon: timedelta,
) -> float | None:
    anchor = points[anchor_index]
    if anchor.price <= 0:
        return None
    end = anchor.timestamp + horizon
    last = bisect_right(times, end) - 1
    if last <= anchor_index or points[last].timestamp < end:
        return None
    base = log(anchor.price)
    return max(
        abs(log(points[index].price) - base)
        for index in range(anchor_index + 1, last + 1)
        if points[index].price > 0
    )


def trailing_volatility(
    points: Sequence[PricePoint],
    window_minutes: int,
) -> list[float | None]:
    """Causal realized volatility (std of per-step log returns) per point.

    Only returns ending at or before each point are used.
    """
    count_points = len(points)
    output: list[float | None] = [None] * count_points
    returns = [0.0] * count_points
    for index in range(1, count_points):
        previous = points[index - 1].price
        current = points[index].price
        if previous > 0 and current > 0:
            returns[index] = log(current / previous)

    window = timedelta(minutes=window_minutes)
    total = 0.0
    total_sq = 0.0
    start = 1
    for index in range(1, count_points):
        total += returns[index]
        total_sq += returns[index] ** 2
        while (
            start < index
            and points[index].timestamp - points[start - 1].timestamp > window
        ):
            total -= returns[start]
            total_sq -= returns[start] ** 2
            start += 1
        count = index - start + 1
        span = points[index].timestamp - points[start - 1].timestamp
        if count >= 10 and span >= window * 0.9:
            mean = total / count
            variance = total_sq / count - mean * mean
            if variance > 0:
                output[index] = sqrt(variance)
    return output


def _tercile_coverage(
    records: list[tuple[float, bool]],
) -> tuple[float | None, float | None, float | None]:
    if len(records) < 3:
        return None, None, None
    ordered = sorted(records, key=lambda item: item[0])
    size = len(ordered)
    groups = (
        ordered[: size // 3],
        ordered[size // 3 : 2 * size // 3],
        ordered[2 * size // 3 :],
    )
    low, mid, high = (
        sum(hit for _, hit in group) / len(group) if group else None
        for group in groups
    )
    return low, mid, high


def evaluate_walk_forward_range(
    points: Sequence[PricePoint],
    *,
    horizon_minutes: int,
    target_coverage: float = 0.70,
    lookback_days: int = 30,
    minimum_calibration_count: int = 20,
    volatility_scaled: bool = False,
    volatility_window_minutes: int | None = None,
) -> RangeCoverageResult:
    """Evaluate a strictly causal rolling range forecast.

    Calibration uses only anchors whose full future horizon finished before the
    evaluation anchor.  Anchors are spaced by the forecast horizon to reduce
    heavy overlap between observations.
    """
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")

    ordered = sorted(points, key=lambda item: item.timestamp)
    if len(ordered) < 2:
        return RangeCoverageResult(
            horizon_minutes,
            target_coverage,
            0,
            0,
            0.0,
            0.0,
            0.0,
            minimum_calibration_count,
            volatility_scaled,
        )

    horizon = timedelta(minutes=horizon_minutes)
    lookback = timedelta(days=lookback_days)
    times = [item.timestamp for item in ordered]
    vol_window = volatility_window_minutes or min(
        1440,
        max(60, 4 * horizon_minutes),
    )
    volatility = trailing_volatility(ordered, vol_window)

    anchor_indices: list[int] = []
    next_anchor = ordered[0].timestamp
    for index, point in enumerate(ordered):
        if point.timestamp >= next_anchor:
            anchor_indices.append(index)
            next_anchor = point.timestamp + horizon

    excursions: dict[int, float] = {}
    for index in anchor_indices:
        value = _future_excursion(ordered, times, index, horizon)
        if value is None:
            continue
        if volatility_scaled:
            anchor_vol = volatility[index]
            if anchor_vol is None:
                continue
            value = value / anchor_vol
        excursions[index] = value

    covered = 0
    evaluated = 0
    widths: list[float] = []
    radii: list[float] = []
    regime_records: list[tuple[float, bool]] = []

    for index in anchor_indices:
        actual = excursions.get(index)
        if actual is None:
            continue
        now = ordered[index].timestamp
        oldest = now - lookback

        calibration = [
            excursion
            for prior_index, excursion in excursions.items()
            if prior_index < index
            and ordered[prior_index].timestamp >= oldest
            and ordered[prior_index].timestamp + horizon <= now
        ]
        if len(calibration) < minimum_calibration_count:
            continue

        radius = _higher_quantile(calibration, target_coverage)
        hit = actual <= radius
        if volatility_scaled:
            # Convert from volatility units back to log-price units.
            radius *= volatility[index]

        evaluated += 1
        covered += int(hit)
        if volatility[index] is not None:
            regime_records.append((volatility[index], hit))

        up = exp(radius) - 1.0
        down = 1.0 - exp(-radius)
        radii.append((up + down) * 50.0)
        widths.append((up + down) * 100.0)

    empirical = covered / evaluated if evaluated else 0.0
    low, mid, high = _tercile_coverage(regime_records)
    return RangeCoverageResult(
        horizon_minutes=horizon_minutes,
        target_coverage=target_coverage,
        evaluated=evaluated,
        covered=covered,
        empirical_coverage=empirical,
        average_full_width_pct=(sum(widths) / len(widths) if widths else 0.0),
        average_radius_pct=(sum(radii) / len(radii) if radii else 0.0),
        minimum_calibration_count=minimum_calibration_count,
        volatility_scaled=volatility_scaled,
        coverage_low_vol=low,
        coverage_mid_vol=mid,
        coverage_high_vol=high,
    )



def estimate_live_range(
    points: Sequence[PricePoint],
    *,
    horizon_minutes: int,
    target_coverage: float = 0.70,
    lookback_days: int = 30,
    minimum_calibration_count: int = 20,
    volatility_scaled: bool = True,
    volatility_window_minutes: int | None = None,
) -> LiveRangeEstimate | None:
    """Estimate a current symmetric price band using only completed past paths.

    The last point is the live anchor. Calibration samples must have completed
    their full future horizon before that anchor, so the estimate is causal.
    """
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    ordered = sorted(points, key=lambda item: item.timestamp)
    if len(ordered) < 3:
        return None

    horizon = timedelta(minutes=horizon_minutes)
    lookback = timedelta(days=lookback_days)
    times = [item.timestamp for item in ordered]
    current_index = len(ordered) - 1
    current = ordered[current_index]
    if current.price <= 0:
        return None

    vol_window = volatility_window_minutes or min(
        1440,
        max(60, 4 * horizon_minutes),
    )
    volatility = trailing_volatility(ordered, vol_window)
    current_vol = volatility[current_index]
    if volatility_scaled and current_vol is None:
        return None

    calibration: list[float] = []
    next_anchor = max(0, current_index - 1)
    # Walk backwards and keep roughly non-overlapping anchors.
    last_used_time: datetime | None = None
    while next_anchor >= 0:
        anchor = ordered[next_anchor]
        if anchor.timestamp < current.timestamp - lookback:
            break
        if anchor.timestamp + horizon > current.timestamp:
            next_anchor -= 1
            continue
        if last_used_time is not None and last_used_time - anchor.timestamp < horizon:
            next_anchor -= 1
            continue

        excursion = _future_excursion(ordered, times, next_anchor, horizon)
        if excursion is not None:
            if volatility_scaled:
                anchor_vol = volatility[next_anchor]
                if anchor_vol is not None and anchor_vol > 0:
                    calibration.append(excursion / anchor_vol)
                    last_used_time = anchor.timestamp
            else:
                calibration.append(excursion)
                last_used_time = anchor.timestamp
        next_anchor -= 1

    if len(calibration) < minimum_calibration_count:
        return None

    radius = _higher_quantile(calibration, target_coverage)
    if volatility_scaled:
        radius *= current_vol

    lower = current.price * exp(-radius)
    upper = current.price * exp(radius)
    return LiveRangeEstimate(
        horizon_minutes=horizon_minutes,
        target_coverage=target_coverage,
        lower=lower,
        upper=upper,
        radius_pct=((exp(radius) - exp(-radius)) * 50.0),
        calibration_count=len(calibration),
        volatility_scaled=volatility_scaled,
    )
