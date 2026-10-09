from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.predictions.models import Prediction, PredictionDirection
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(frozen=True, slots=True)
class FeatureEdgeStats:
    horizon_seconds: int
    direction: str
    feature_name: str
    relation: str
    samples: int
    cost_clear_rate_pct: Decimal
    median_mfe_pct: Decimal
    median_mae_pct: Decimal


@dataclass(frozen=True, slots=True)
class AgreementEdgeStats:
    horizon_seconds: int
    direction: str
    supporting_features: int
    samples: int
    cost_clear_rate_pct: Decimal
    median_mfe_pct: Decimal
    median_mae_pct: Decimal


def build_feature_edge_stats(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
) -> tuple[FeatureEdgeStats, ...]:
    points_by_symbol = _points_by_symbol(price_points)
    grouped: dict[
        tuple[int, str, str, str],
        list[tuple[Decimal, Decimal]],
    ] = defaultdict(list)

    for prediction in result.predictions:
        contributions = prediction.feature_contributions or {}
        excursion = _prediction_excursion(
            prediction,
            points_by_symbol.get(prediction.symbol.upper(), []),
        )
        if excursion is None:
            continue

        direction_sign = (
            Decimal("1")
            if prediction.direction == PredictionDirection.LONG
            else Decimal("-1")
        )

        for feature_name, contribution in contributions.items():
            aligned = contribution * direction_sign
            if aligned > 0:
                relation = "support"
            elif aligned < 0:
                relation = "oppose"
            else:
                relation = "neutral"

            grouped[
                (
                    prediction.horizon_seconds,
                    prediction.direction.value,
                    feature_name,
                    relation,
                )
            ].append(excursion)

    return tuple(
        FeatureEdgeStats(
            horizon_seconds=key[0],
            direction=key[1],
            feature_name=key[2],
            relation=key[3],
            samples=len(values),
            **_summary(values, round_trip_cost_pct),
        )
        for key, values in sorted(grouped.items())
    )


def build_agreement_edge_stats(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
) -> tuple[AgreementEdgeStats, ...]:
    points_by_symbol = _points_by_symbol(price_points)
    grouped: dict[
        tuple[int, str, int],
        list[tuple[Decimal, Decimal]],
    ] = defaultdict(list)

    for prediction in result.predictions:
        contributions = prediction.feature_contributions or {}
        excursion = _prediction_excursion(
            prediction,
            points_by_symbol.get(prediction.symbol.upper(), []),
        )
        if excursion is None:
            continue

        direction_sign = (
            Decimal("1")
            if prediction.direction == PredictionDirection.LONG
            else Decimal("-1")
        )
        supporting_features = sum(
            1
            for contribution in contributions.values()
            if contribution * direction_sign > 0
        )

        grouped[
            (
                prediction.horizon_seconds,
                prediction.direction.value,
                supporting_features,
            )
        ].append(excursion)

    return tuple(
        AgreementEdgeStats(
            horizon_seconds=key[0],
            direction=key[1],
            supporting_features=key[2],
            samples=len(values),
            **_summary(values, round_trip_cost_pct),
        )
        for key, values in sorted(grouped.items())
    )


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
    return max(Decimal("0"), max(returns)), max(Decimal("0"), -min(returns))


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
    clears_cost = sum(1 for mfe, _ in values if mfe > round_trip_cost_pct)
    return {
        "cost_clear_rate_pct": (
            Decimal(clears_cost)
            / Decimal(len(values))
            * Decimal("100")
        ),
        "median_mfe_pct": _median(mfe_values),
        "median_mae_pct": _median(mae_values),
    }


def _median(values: list[Decimal]) -> Decimal:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")
