"""Causal price-range calibration from realized future excursion.

The forecast is deliberately direction-agnostic.  For an anchor price P and
horizon H, it estimates a radius q from *past* maximum absolute log excursions
and returns [P * exp(-q), P * exp(q)].  A hit means the entire observed future
path through H stayed inside that band.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, exp, log
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    price: float


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


def evaluate_walk_forward_range(
    points: Sequence[PricePoint],
    *,
    horizon_minutes: int,
    target_coverage: float = 0.70,
    lookback_days: int = 30,
    minimum_calibration_count: int = 20,
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
        )

    horizon = timedelta(minutes=horizon_minutes)
    lookback = timedelta(days=lookback_days)
    times = [item.timestamp for item in ordered]

    anchor_indices: list[int] = []
    next_anchor = ordered[0].timestamp
    for index, point in enumerate(ordered):
        if point.timestamp >= next_anchor:
            anchor_indices.append(index)
            next_anchor = point.timestamp + horizon

    excursions: dict[int, float] = {}
    for index in anchor_indices:
        value = _future_excursion(ordered, times, index, horizon)
        if value is not None:
            excursions[index] = value

    covered = 0
    evaluated = 0
    widths: list[float] = []
    radii: list[float] = []

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
        evaluated += 1
        covered += int(actual <= radius)

        up = exp(radius) - 1.0
        down = 1.0 - exp(-radius)
        radii.append((up + down) * 50.0)
        widths.append((up + down) * 100.0)

    empirical = covered / evaluated if evaluated else 0.0
    return RangeCoverageResult(
        horizon_minutes=horizon_minutes,
        target_coverage=target_coverage,
        evaluated=evaluated,
        covered=covered,
        empirical_coverage=empirical,
        average_full_width_pct=(sum(widths) / len(widths) if widths else 0.0),
        average_radius_pct=(sum(radii) / len(radii) if radii else 0.0),
        minimum_calibration_count=minimum_calibration_count,
    )
