from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True, slots=True)
class _TimedReturn:
    timestamp: datetime
    return_pct: Decimal


def _candidate_timed_returns(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    take_profit_pct: Decimal,
    stop_loss_pct: Decimal,
    round_trip_cost_pct: Decimal,
) -> tuple[_TimedReturn, ...]:
    points_by_symbol: dict[str, list[ReplayPricePoint]] = {}
    for point in price_points:
        points_by_symbol.setdefault(point.symbol.upper(), []).append(point)

    timestamps_by_symbol: dict[str, list[datetime]] = {}
    for symbol, points in points_by_symbol.items():
        points.sort(key=lambda item: item.timestamp)
        timestamps_by_symbol[symbol] = [point.timestamp for point in points]

    returns: list[_TimedReturn] = []

    for prediction in result.predictions:
        symbol = prediction.symbol.upper()
        points = points_by_symbol.get(symbol, [])
        timestamps = timestamps_by_symbol.get(symbol, [])
        if not points:
            continue

        # O(log N) window lookup instead of scanning every price point for
        # every prediction. The slice is then limited to the prediction's
        # actual lifetime.
        start_index = bisect_right(timestamps, prediction.created_at)
        end_index = bisect_right(timestamps, prediction.expires_at)
        if start_index >= end_index:
            continue

        closed_return: Decimal | None = None
        final_directional: Decimal | None = None

        for point in points[start_index:end_index]:
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
            final_directional = directional

            if directional >= take_profit_pct:
                closed_return = take_profit_pct - round_trip_cost_pct
                break
            if directional <= -stop_loss_pct:
                closed_return = -stop_loss_pct - round_trip_cost_pct
                break

        if closed_return is None:
            if final_directional is None:
                continue
            closed_return = final_directional - round_trip_cost_pct

        returns.append(
            _TimedReturn(
                timestamp=prediction.created_at,
                return_pct=closed_return,
            )
        )

    return tuple(returns)


def build_candidate_trade_returns(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    take_profit_pct: Decimal,
    stop_loss_pct: Decimal,
    round_trip_cost_pct: Decimal,
) -> tuple[Decimal, ...]:
    """Return one outcome per prediction.

    This is useful for diagnostics, but the observations are not assumed to be
    statistically independent. Use build_candidate_independent_returns for
    robustness / promotion decisions.
    """

    return tuple(
        item.return_pct
        for item in _candidate_timed_returns(
            result,
            price_points,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            round_trip_cost_pct=round_trip_cost_pct,
        )
    )


def build_candidate_independent_returns(
    result: ReplayResult,
    price_points: list[ReplayPricePoint],
    *,
    take_profit_pct: Decimal,
    stop_loss_pct: Decimal,
    round_trip_cost_pct: Decimal,
    bucket_minutes: int = 60,
) -> tuple[Decimal, ...]:
    """Collapse correlated predictions into time buckets.

    The live engine can emit overlapping predictions every minute. Treating
    those predictions as independent bootstrap samples creates pseudo-
    replication because many of them observe the same underlying BTC move.
    We therefore collapse all prediction returns in the same fixed UTC time
    bucket into a single mean return before bootstrap robustness analysis.
    """

    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")

    timed_returns = _candidate_timed_returns(
        result,
        price_points,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        round_trip_cost_pct=round_trip_cost_pct,
    )
    if not timed_returns:
        return ()

    bucket_seconds = bucket_minutes * 60
    grouped: dict[int, list[Decimal]] = {}

    for item in timed_returns:
        epoch_seconds = int(item.timestamp.timestamp())
        bucket_key = epoch_seconds // bucket_seconds
        grouped.setdefault(bucket_key, []).append(item.return_pct)

    return tuple(
        sum(values, Decimal("0")) / Decimal(len(values))
        for _, values in sorted(grouped.items())
    )


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
            reasons=("no_independent_returns",),
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
    positive_rate = Decimal(positive) / Decimal(simulations)

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
            f"insufficient_independent_samples:{sample_size}/{minimum_samples}"
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
