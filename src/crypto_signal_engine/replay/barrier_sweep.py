from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.predictions.models import Prediction, PredictionDirection
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(frozen=True, slots=True)
class BarrierSweepRow:
    horizon_seconds: int
    direction: str
    take_profit_pct: Decimal
    stop_loss_pct: Decimal
    samples: int
    take_profit: int
    stop_loss: int
    no_touch: int
    expectancy_pct: Decimal
    pre_cost_expectancy_pct: Decimal
    execution_cost_pct: Decimal
    profit_factor: Decimal | None


def build_barrier_sweep(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    round_trip_cost_pct: Decimal,
    take_profit_grid: tuple[Decimal, ...] = (
        Decimal("0.15"),
        Decimal("0.18"),
        Decimal("0.20"),
        Decimal("0.25"),
        Decimal("0.30"),
        Decimal("0.40"),
    ),
    stop_loss_grid: tuple[Decimal, ...] = (
        Decimal("0.10"),
        Decimal("0.12"),
        Decimal("0.15"),
        Decimal("0.18"),
        Decimal("0.20"),
        Decimal("0.25"),
        Decimal("0.30"),
    ),
) -> tuple[BarrierSweepRow, ...]:
    grouped: dict[tuple[int, str], list[Prediction]] = defaultdict(list)
    for prediction in result.predictions:
        grouped[
            (prediction.horizon_seconds, prediction.direction.value)
        ].append(prediction)

    points_by_symbol: dict[str, list[ReplayPricePoint]] = defaultdict(list)
    for point in price_points:
        points_by_symbol[point.symbol.upper()].append(point)
    for points in points_by_symbol.values():
        points.sort(key=lambda item: item.timestamp)

    rows = []
    for (horizon_seconds, direction), predictions in sorted(grouped.items()):
        for take_profit_pct in take_profit_grid:
            for stop_loss_pct in stop_loss_grid:
                outcomes = [
                    _simulate_prediction(
                        prediction,
                        points_by_symbol.get(prediction.symbol.upper(), []),
                        take_profit_pct=take_profit_pct,
                        stop_loss_pct=stop_loss_pct,
                        round_trip_cost_pct=round_trip_cost_pct,
                    )
                    for prediction in predictions
                ]
                valid = [outcome for outcome in outcomes if outcome is not None]
                if not valid:
                    continue

                returns = [outcome[1] for outcome in valid]
                pre_cost_returns = [
                    value + round_trip_cost_pct
                    for value in returns
                ]
                gross_profit = sum(
                    (value for value in returns if value > 0),
                    Decimal("0"),
                )
                gross_loss = -sum(
                    (value for value in returns if value < 0),
                    Decimal("0"),
                )
                profit_factor = (
                    gross_profit / gross_loss
                    if gross_loss > 0
                    else None
                )

                rows.append(
                    BarrierSweepRow(
                        horizon_seconds=horizon_seconds,
                        direction=direction,
                        take_profit_pct=take_profit_pct,
                        stop_loss_pct=stop_loss_pct,
                        samples=len(valid),
                        take_profit=sum(1 for outcome, _ in valid if outcome == "tp"),
                        stop_loss=sum(1 for outcome, _ in valid if outcome == "sl"),
                        no_touch=sum(1 for outcome, _ in valid if outcome == "nt"),
                        expectancy_pct=(
                            sum(returns, Decimal("0"))
                            / Decimal(len(returns))
                        ),
                        pre_cost_expectancy_pct=(
                            sum(pre_cost_returns, Decimal("0"))
                            / Decimal(len(pre_cost_returns))
                        ),
                        execution_cost_pct=round_trip_cost_pct,
                        profit_factor=profit_factor,
                    )
                )

    return tuple(rows)


def top_barrier_sweep_rows(
    rows: tuple[BarrierSweepRow, ...],
    *,
    per_group: int = 5,
) -> tuple[BarrierSweepRow, ...]:
    grouped: dict[tuple[int, str], list[BarrierSweepRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.horizon_seconds, row.direction)].append(row)

    selected = []
    for key in sorted(grouped):
        ranked = sorted(
            grouped[key],
            key=lambda row: (
                row.expectancy_pct,
                row.profit_factor or Decimal("0"),
                -row.take_profit_pct,
                -row.stop_loss_pct,
            ),
            reverse=True,
        )
        selected.extend(ranked[:per_group])

    return tuple(selected)


def _simulate_prediction(
    prediction: Prediction,
    points: list[ReplayPricePoint],
    *,
    take_profit_pct: Decimal,
    stop_loss_pct: Decimal,
    round_trip_cost_pct: Decimal,
) -> tuple[str, Decimal] | None:
    path = [
        point
        for point in points
        if prediction.created_at < point.timestamp <= prediction.expires_at
    ]
    if not path:
        return None

    for point in path:
        directional_return = _directional_return_pct(
            prediction,
            point.price,
        )
        if directional_return >= take_profit_pct:
            return "tp", take_profit_pct - round_trip_cost_pct
        if directional_return <= -stop_loss_pct:
            return "sl", -stop_loss_pct - round_trip_cost_pct

    final_return = _directional_return_pct(
        prediction,
        path[-1].price,
    )
    return "nt", final_return - round_trip_cost_pct


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
