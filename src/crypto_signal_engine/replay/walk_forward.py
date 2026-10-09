from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.replay.barrier_sweep import (
    BarrierSweepRow,
    build_barrier_sweep,
)
from crypto_signal_engine.replay.candidate_gates import CandidateGate
from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult
from crypto_signal_engine.replay.robustness import (
    bootstrap_candidate_robustness,
    build_candidate_independent_returns,
)


@dataclass(frozen=True, slots=True)
class CandidateWindowResult:
    window_index: int
    gate: CandidateGate
    result: ReplayResult
    price_points: tuple[ReplayPricePoint, ...]
    row: BarrierSweepRow | None


@dataclass(frozen=True, slots=True)
class CandidateLeaderboardRow:
    candidate_name: str
    windows_tested: int
    active_windows: int
    positive_windows: int
    trades: int
    expectancy_pct: Decimal | None
    profit_factor: Decimal | None
    robustness_passed: bool
    robustness_positive_expectancy_rate: Decimal | None
    robustness_p05_expectancy_pct: Decimal | None
    robustness_independent_samples: int
    promotion_ready: bool
    reasons: tuple[str, ...]


def build_candidate_leaderboard(
    windows: list[CandidateWindowResult],
    *,
    minimum_trades: int = 30,
    minimum_active_windows: int = 3,
    minimum_positive_window_ratio: Decimal = Decimal("0.60"),
    minimum_profit_factor: Decimal = Decimal("1.10"),
    minimum_robustness_positive_expectancy_rate: Decimal = Decimal("0.80"),
    minimum_robustness_samples: int = 20,
    robustness_simulations: int = 1000,
    round_trip_cost_pct: Decimal = Decimal("0.12"),
) -> tuple[CandidateLeaderboardRow, ...]:
    by_candidate: dict[str, list[CandidateWindowResult]] = {}
    for item in windows:
        by_candidate.setdefault(item.gate.name, []).append(item)

    rows: list[CandidateLeaderboardRow] = []
    for candidate_name, items in sorted(by_candidate.items()):
        gate = items[0].gate
        if (
            gate.frozen_take_profit_pct is None
            or gate.frozen_stop_loss_pct is None
        ):
            continue

        active = [
            item
            for item in items
            if item.row is not None and item.row.samples > 0
        ]
        positive_windows = sum(
            1
            for item in active
            if item.row is not None and item.row.expectancy_pct > 0
        )

        predictions = tuple(
            prediction
            for item in active
            for prediction in item.result.predictions
        )
        evaluations = tuple(
            evaluation
            for item in active
            for evaluation in item.result.evaluations
        )
        open_predictions = tuple(
            prediction
            for item in active
            for prediction in item.result.open_predictions
        )
        price_points = [
            point
            for item in active
            for point in item.price_points
        ]

        aggregate_row = None
        if predictions and price_points:
            aggregate_result = ReplayResult(
                decisions=(),
                predictions=predictions,
                evaluations=evaluations,
                open_predictions=open_predictions,
            )
            aggregate_rows = build_barrier_sweep(
                aggregate_result,
                price_points,
                round_trip_cost_pct=round_trip_cost_pct,
                take_profit_grid=(gate.frozen_take_profit_pct,),
                stop_loss_grid=(gate.frozen_stop_loss_pct,),
            )
            aggregate_row = aggregate_rows[0] if aggregate_rows else None

        trades = aggregate_row.samples if aggregate_row is not None else 0
        expectancy = (
            aggregate_row.expectancy_pct
            if aggregate_row is not None
            else None
        )
        profit_factor = (
            aggregate_row.profit_factor
            if aggregate_row is not None
            else None
        )

        independent_returns = build_candidate_independent_returns(
            aggregate_result
            if predictions and price_points
            else ReplayResult(
                decisions=(),
                predictions=(),
                evaluations=(),
                open_predictions=(),
            ),
            price_points,
            take_profit_pct=gate.frozen_take_profit_pct,
            stop_loss_pct=gate.frozen_stop_loss_pct,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        robustness = bootstrap_candidate_robustness(
            independent_returns,
            simulations=robustness_simulations,
            minimum_positive_expectancy_rate=(
                minimum_robustness_positive_expectancy_rate
            ),
            minimum_samples=minimum_robustness_samples,
        )

        reasons: list[str] = []
        if trades < minimum_trades:
            reasons.append(f"trades:{trades}/{minimum_trades}")
        if len(active) < minimum_active_windows:
            reasons.append(
                f"active_windows:{len(active)}/{minimum_active_windows}"
            )
        positive_ratio = (
            Decimal(positive_windows) / Decimal(len(active))
            if active
            else Decimal("0")
        )
        if positive_ratio < minimum_positive_window_ratio:
            reasons.append(
                "positive_window_ratio:"
                f"{positive_ratio:.2f}/{minimum_positive_window_ratio:.2f}"
            )
        if expectancy is None or expectancy <= 0:
            reasons.append(
                "expectancy:not_positive"
                if expectancy is None
                else f"expectancy:{expectancy:+.4f}%"
            )
        if profit_factor is None:
            reasons.append("profit_factor:not_available")
        elif profit_factor < minimum_profit_factor:
            reasons.append(
                f"profit_factor:{profit_factor:.3f}/"
                f"{minimum_profit_factor:.3f}"
            )
        if not robustness.passed:
            reasons.append(
                "robustness:" + "|".join(robustness.reasons)
            )

        rows.append(
            CandidateLeaderboardRow(
                candidate_name=candidate_name,
                windows_tested=len(items),
                active_windows=len(active),
                positive_windows=positive_windows,
                trades=trades,
                expectancy_pct=expectancy,
                profit_factor=profit_factor,
                robustness_passed=robustness.passed,
                robustness_positive_expectancy_rate=(
                    robustness.positive_expectancy_rate
                ),
                robustness_p05_expectancy_pct=(
                    robustness.p05_expectancy_pct
                ),
                robustness_independent_samples=robustness.samples,
                promotion_ready=not reasons,
                reasons=tuple(reasons),
            )
        )

    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.promotion_ready,
                row.expectancy_pct or Decimal("-999"),
                row.trades,
            ),
            reverse=True,
        )
    )
