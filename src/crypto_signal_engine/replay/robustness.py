from dataclasses import dataclass
from decimal import Decimal
import random

from crypto_signal_engine.predictions.models import PredictionDirection
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(frozen=True, slots=True)
class CandidateRobustnessResult:
    samples: int
    simulations: int
    positive_expectancy_rate: Decimal
    p05_expectancy_pct: Decimal
    median_expectancy_pct: Decimal
    p95_expectancy_pct: Decimal
    worst_expectancy_pct: Decimal
    passed: bool
    reasons: tuple[str, ...]


def build_candidate_trade_returns(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    take_profit_pct: Decimal,
    stop_loss_pct: Decimal,
    round_trip_cost_pct: Decimal,
) -> tuple[Decimal, ...]:
    points_by_symbol: dict[str, list[ReplayPricePoint]] = {}
    for point in price_points:
        points_by_symbol.setdefault(point.symbol.upper(), []).append(point)
    for points in points_by_symbol.values():
        points.sort(key=lambda item: item.timestamp)

    returns: list[Decimal] = []
    for prediction in result.predictions:
        path = [
            point
            for point in points_by_symbol.get(prediction.symbol.upper(), [])
            if prediction.created_at < point.timestamp <= prediction.expires_at
        ]
        if not path:
            continue

        closed = False
        for point in path:
            raw = (
                (point.price - prediction.entry_price)
                / prediction.entry_price
                * Decimal("100")
            )
            directional = (
                -raw
                if prediction.direction == PredictionDirection.SHORT
                else raw
            )
            if directional >= take_profit_pct:
                returns.append(take_profit_pct - round_trip_cost_pct)
                closed = True
                break
            if directional <= -stop_loss_pct:
                returns.append(-stop_loss_pct - round_trip_cost_pct)
                closed = True
                break

        if closed:
            continue

        final_raw = (
            (path[-1].price - prediction.entry_price)
            / prediction.entry_price
            * Decimal("100")
        )
        final_directional = (
            -final_raw
            if prediction.direction == PredictionDirection.SHORT
            else final_raw
        )
        returns.append(final_directional - round_trip_cost_pct)

    return tuple(returns)


def bootstrap_candidate_robustness(
    trade_returns: tuple[Decimal, ...],
    *,
    simulations: int = 1000,
    seed: int = 1337,
    minimum_positive_expectancy_rate: Decimal = Decimal("0.80"),
    minimum_samples: int = 20,
) -> CandidateRobustnessResult:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    if minimum_samples <= 0:
        raise ValueError("minimum_samples must be positive")
    if not trade_returns:
        return CandidateRobustnessResult(
            samples=0,
            simulations=simulations,
            positive_expectancy_rate=Decimal("0"),
            p05_expectancy_pct=Decimal("0"),
            median_expectancy_pct=Decimal("0"),
            p95_expectancy_pct=Decimal("0"),
            worst_expectancy_pct=Decimal("0"),
            passed=False,
            reasons=("no_trade_returns",),
        )

    rng = random.Random(seed)
    sample_size = len(trade_returns)
    expectancies: list[Decimal] = []

    for _ in range(simulations):
        sample = [
            trade_returns[rng.randrange(sample_size)]
            for _ in range(sample_size)
        ]
        expectancies.append(
            sum(sample, Decimal("0")) / Decimal(sample_size)
        )

    expectancies.sort()
    positive = sum(1 for value in expectancies if value > 0)
    positive_rate = (
        Decimal(positive) / Decimal(simulations)
    )

    def percentile(fraction: Decimal) -> Decimal:
        if len(expectancies) == 1:
            return expectancies[0]
        index = int(
            (Decimal(len(expectancies) - 1) * fraction)
            .to_integral_value(rounding="ROUND_FLOOR")
        )
        return expectancies[index]

    p05 = percentile(Decimal("0.05"))
    p50 = percentile(Decimal("0.50"))
    p95 = percentile(Decimal("0.95"))

    reasons: list[str] = []
    if sample_size < minimum_samples:
        reasons.append(
            f"insufficient_samples:{sample_size}/{minimum_samples}"
        )
    if positive_rate < minimum_positive_expectancy_rate:
        reasons.append(
            "positive_expectancy_rate_below_minimum:"
            f"{positive_rate:.2f}/{minimum_positive_expectancy_rate:.2f}"
        )
    if p05 <= 0:
        reasons.append(f"p05_expectancy_not_positive:{p05:+.4f}%")

    return CandidateRobustnessResult(
        samples=sample_size,
        simulations=simulations,
        positive_expectancy_rate=positive_rate,
        p05_expectancy_pct=p05,
        median_expectancy_pct=p50,
        p95_expectancy_pct=p95,
        worst_expectancy_pct=expectancies[0],
        passed=not reasons,
        reasons=tuple(reasons),
    )
