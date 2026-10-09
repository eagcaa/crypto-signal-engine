from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.predictions.models import Prediction, PredictionDirection
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(frozen=True, slots=True)
class ExcursionStats:
    horizon_seconds: int
    direction: str
    samples: int
    median_mfe_pct: Decimal
    p75_mfe_pct: Decimal
    median_mae_pct: Decimal
    p90_mae_pct: Decimal
    cost_clear_rate_pct: Decimal


def build_excursion_stats(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
) -> tuple[ExcursionStats, ...]:
    grouped: dict[tuple[int, str], list[tuple[Decimal, Decimal]]] = defaultdict(list)

    points_by_symbol: dict[str, list[ReplayPricePoint]] = defaultdict(list)
    for point in price_points:
        points_by_symbol[point.symbol.upper()].append(point)

    for points in points_by_symbol.values():
        points.sort(key=lambda item: item.timestamp)

    for prediction in result.predictions:
        points = points_by_symbol.get(prediction.symbol.upper(), [])
        excursion = _prediction_excursion(prediction, points)
        if excursion is None:
            continue
        grouped[
            (prediction.horizon_seconds, prediction.direction.value)
        ].append(excursion)

    rows = []
    for (horizon_seconds, direction), values in sorted(grouped.items()):
        mfe_values = sorted(item[0] for item in values)
        mae_values = sorted(item[1] for item in values)
        clears_cost = sum(
            1
            for mfe, _ in values
            if mfe > round_trip_cost_pct
        )
        rows.append(
            ExcursionStats(
                horizon_seconds=horizon_seconds,
                direction=direction,
                samples=len(values),
                median_mfe_pct=_percentile(mfe_values, Decimal("0.50")),
                p75_mfe_pct=_percentile(mfe_values, Decimal("0.75")),
                median_mae_pct=_percentile(mae_values, Decimal("0.50")),
                p90_mae_pct=_percentile(mae_values, Decimal("0.90")),
                cost_clear_rate_pct=(
                    Decimal(clears_cost)
                    / Decimal(len(values))
                    * Decimal("100")
                ),
            )
        )

    return tuple(rows)


def _prediction_excursion(
    prediction: Prediction,
    points: list[ReplayPricePoint],
) -> tuple[Decimal, Decimal] | None:
    returns = [
        _directional_return_pct(prediction, point.price)
        for point in points
        if prediction.created_at < point.timestamp <= prediction.expires_at
    ]
    if not returns:
        return None

    favorable = max(Decimal("0"), max(returns))
    adverse = max(Decimal("0"), -min(returns))
    return favorable, adverse


def _directional_return_pct(
    prediction: Prediction,
    price: Decimal,
) -> Decimal:
    raw = (
        (price - prediction.entry_price)
        / prediction.entry_price
        * Decimal("100")
    )
    if prediction.direction == PredictionDirection.SHORT:
        return -raw
    return raw


def _percentile(
    values: list[Decimal],
    percentile: Decimal,
) -> Decimal:
    if not values:
        return Decimal("0")
    if len(values) == 1:
        return values[0]

    position = percentile * Decimal(len(values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - Decimal(lower_index)

    lower = values[lower_index]
    upper = values[upper_index]
    return lower + (upper - lower) * fraction
