from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
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


@dataclass(frozen=True, slots=True)
class ScoreExcursionStats:
    horizon_seconds: int
    direction: str
    lower_bound: Decimal
    upper_bound: Decimal | None
    samples: int
    median_mfe_pct: Decimal
    p75_mfe_pct: Decimal
    median_mae_pct: Decimal
    p90_mae_pct: Decimal
    cost_clear_rate_pct: Decimal


@dataclass(frozen=True, slots=True)
class RegimeExcursionStats:
    horizon_seconds: int
    direction: str
    trend_regime: str
    volatility_regime: str
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



def build_score_excursion_stats(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
) -> tuple[ScoreExcursionStats, ...]:
    grouped: dict[
        tuple[int, str, Decimal, Decimal | None],
        list[tuple[Decimal, Decimal]],
    ] = defaultdict(list)
    points_by_symbol = _points_by_symbol(price_points)

    for prediction in result.predictions:
        excursion = _prediction_excursion(
            prediction,
            points_by_symbol.get(prediction.symbol.upper(), []),
        )
        if excursion is None:
            continue
        lower_bound, upper_bound = _score_bin(abs(prediction.raw_score))
        grouped[
            (
                prediction.horizon_seconds,
                prediction.direction.value,
                lower_bound,
                upper_bound,
            )
        ].append(excursion)

    return tuple(
        ScoreExcursionStats(
            horizon_seconds=key[0],
            direction=key[1],
            lower_bound=key[2],
            upper_bound=key[3],
            samples=len(values),
            **_summary(values, round_trip_cost_pct),
        )
        for key, values in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2]),
        )
    )


def build_regime_excursion_stats(
    result: ReplayResult,
    feature_snapshots: list[ResearchFeatureSnapshot],
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
) -> tuple[RegimeExcursionStats, ...]:
    grouped: dict[
        tuple[int, str, str, str],
        list[tuple[Decimal, Decimal]],
    ] = defaultdict(list)
    points_by_symbol = _points_by_symbol(price_points)
    feature_by_key = {
        (feature.symbol.upper(), feature.timestamp): feature
        for feature in feature_snapshots
    }

    for prediction in result.predictions:
        feature = feature_by_key.get(
            (prediction.symbol.upper(), prediction.created_at)
        )
        if feature is None:
            continue

        excursion = _prediction_excursion(
            prediction,
            points_by_symbol.get(prediction.symbol.upper(), []),
        )
        if excursion is None:
            continue

        if prediction.horizon_seconds == 900:
            trend_regime = feature.trend_regime_15m or "unknown"
            volatility_regime = feature.volatility_regime_15m or "unknown"
        else:
            trend_regime = feature.trend_regime_5m or "unknown"
            volatility_regime = feature.volatility_regime_5m or "unknown"

        grouped[
            (
                prediction.horizon_seconds,
                prediction.direction.value,
                trend_regime,
                volatility_regime,
            )
        ].append(excursion)

    return tuple(
        RegimeExcursionStats(
            horizon_seconds=key[0],
            direction=key[1],
            trend_regime=key[2],
            volatility_regime=key[3],
            samples=len(values),
            **_summary(values, round_trip_cost_pct),
        )
        for key, values in sorted(grouped.items())
    )


def _points_by_symbol(
    price_points: list[ReplayPricePoint],
) -> dict[str, list[ReplayPricePoint]]:
    grouped: dict[str, list[ReplayPricePoint]] = defaultdict(list)
    for point in price_points:
        grouped[point.symbol.upper()].append(point)
    for points in grouped.values():
        points.sort(key=lambda item: item.timestamp)
    return grouped


def _summary(
    values: list[tuple[Decimal, Decimal]],
    round_trip_cost_pct: Decimal,
) -> dict[str, Decimal]:
    mfe_values = sorted(item[0] for item in values)
    mae_values = sorted(item[1] for item in values)
    clears_cost = sum(
        1
        for mfe, _ in values
        if mfe > round_trip_cost_pct
    )
    return {
        "median_mfe_pct": _percentile(mfe_values, Decimal("0.50")),
        "p75_mfe_pct": _percentile(mfe_values, Decimal("0.75")),
        "median_mae_pct": _percentile(mae_values, Decimal("0.50")),
        "p90_mae_pct": _percentile(mae_values, Decimal("0.90")),
        "cost_clear_rate_pct": (
            Decimal(clears_cost)
            / Decimal(len(values))
            * Decimal("100")
        ),
    }


def _score_bin(
    absolute_score: Decimal,
) -> tuple[Decimal, Decimal | None]:
    if absolute_score < Decimal("0.25"):
        return Decimal("0.20"), Decimal("0.25")
    if absolute_score < Decimal("0.30"):
        return Decimal("0.25"), Decimal("0.30")
    if absolute_score < Decimal("0.40"):
        return Decimal("0.30"), Decimal("0.40")
    return Decimal("0.40"), None
