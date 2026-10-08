from dataclasses import dataclass

from crypto_signal_engine.replay.report import ReplayReport


@dataclass(frozen=True, slots=True)
class ReplayComparisonRow:
    horizon_seconds: int
    sampled_predictions: int
    sampled_take_profit: int
    sampled_stop_loss: int
    sampled_no_touch: int
    exact_predictions: int
    exact_take_profit: int
    exact_stop_loss: int
    exact_no_touch: int

    @property
    def take_profit_delta(self) -> int:
        return self.exact_take_profit - self.sampled_take_profit

    @property
    def stop_loss_delta(self) -> int:
        return self.exact_stop_loss - self.sampled_stop_loss

    @property
    def no_touch_delta(self) -> int:
        return self.exact_no_touch - self.sampled_no_touch


def compare_replay_reports(
    sampled: ReplayReport,
    exact: ReplayReport,
) -> tuple[ReplayComparisonRow, ...]:
    horizons = sorted(
        set(sampled.by_horizon) | set(exact.by_horizon)
    )
    rows: list[ReplayComparisonRow] = []

    for horizon in horizons:
        sampled_stats = sampled.by_horizon.get(horizon)
        exact_stats = exact.by_horizon.get(horizon)

        rows.append(
            ReplayComparisonRow(
                horizon_seconds=horizon,
                sampled_predictions=(
                    sampled_stats.predictions if sampled_stats else 0
                ),
                sampled_take_profit=(
                    sampled_stats.take_profit if sampled_stats else 0
                ),
                sampled_stop_loss=(
                    sampled_stats.stop_loss if sampled_stats else 0
                ),
                sampled_no_touch=(
                    sampled_stats.expired_no_touch if sampled_stats else 0
                ),
                exact_predictions=(
                    exact_stats.predictions if exact_stats else 0
                ),
                exact_take_profit=(
                    exact_stats.take_profit if exact_stats else 0
                ),
                exact_stop_loss=(
                    exact_stats.stop_loss if exact_stats else 0
                ),
                exact_no_touch=(
                    exact_stats.expired_no_touch if exact_stats else 0
                ),
            )
        )

    return tuple(rows)
