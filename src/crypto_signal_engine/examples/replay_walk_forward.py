import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.collectors.binance import BinanceSpotHistoricalTradeClient
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.db.replay_repository import ReplayDataRepository
from crypto_signal_engine.predictions import (
    CompositePredictionEngine,
    HistoricalCompatiblePredictionEngine,
)
from crypto_signal_engine.replay import (
    CANDIDATE_GATES,
    HISTORICAL_CANDIDATE_GATES,
    CandidateWindowResult,
    ReplayResult,
    ReplayRunner,
    build_barrier_sweep,
    build_candidate_leaderboard,
    filter_replay_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fixed candidate gates through sequential exact replay windows "
            "and print a promotion leaderboard."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--window-hours", type=float, default=12.0)
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--end-offset-hours", type=float, default=0.0)
    parser.add_argument(
        "--end-at",
        default="",
        help="Optional absolute UTC/offset-aware ISO end timestamp.",
    )
    parser.add_argument("--dataset-provenance", default="")
    parser.add_argument("--historical-compatible", action="store_true")
    parser.add_argument("--minimum-trades", type=int, default=30)
    parser.add_argument("--minimum-active-windows", type=int, default=3)
    parser.add_argument(
        "--minimum-positive-window-ratio",
        type=Decimal,
        default=Decimal("0.60"),
    )
    parser.add_argument(
        "--minimum-profit-factor",
        type=Decimal,
        default=Decimal("1.10"),
    )
    return parser.parse_args()


async def run(
    *,
    symbol: str,
    window_hours: float,
    windows: int,
    end_offset_hours: float,
    minimum_trades: int,
    minimum_active_windows: int,
    minimum_positive_window_ratio: Decimal,
    minimum_profit_factor: Decimal,
    end_at: str = "",
    dataset_provenance: str = "",
    historical_compatible: bool = False,
) -> None:
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")
    if windows <= 0:
        raise ValueError("windows must be positive")
    if end_offset_hours < 0:
        raise ValueError("end_offset_hours cannot be negative")

    candidate_gates = (
        HISTORICAL_CANDIDATE_GATES
        if historical_compatible
        else CANDIDATE_GATES
    )
    frozen_gates = tuple(
        gate
        for gate in candidate_gates
        if (
            gate.frozen_take_profit_pct is not None
            and gate.frozen_stop_loss_pct is not None
        )
    )
    if not frozen_gates:
        print("No frozen candidate gates are configured.")
        return

    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        session_factory = create_session_factory(engine)
        repository = ReplayDataRepository(session_factory)
        prediction_engine_cls = (
            HistoricalCompatiblePredictionEngine
            if historical_compatible
            else CompositePredictionEngine
        )
        runner = ReplayRunner(
            prediction_engine=prediction_engine_cls(
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )
        historical_client = BinanceSpotHistoricalTradeClient()

        round_trip_cost_pct = Decimal("2") * (
            settings.paper_fee_pct_per_side
            + settings.paper_slippage_pct_per_side
        )

        if end_at:
            parsed_end = datetime.fromisoformat(end_at)
            if parsed_end.tzinfo is None:
                raise ValueError("--end-at must include a timezone/UTC offset")
            anchor_end = parsed_end.astimezone(UTC)
        else:
            anchor_end = datetime.now(UTC) - timedelta(hours=end_offset_hours)
        window_delta = timedelta(hours=window_hours)
        candidate_windows: list[CandidateWindowResult] = []

        print(
            "WALK_FORWARD "
            f"symbol={symbol.upper()} "
            f"window_hours={window_hours:g} "
            f"windows={windows} "
            f"end_offset_hours={end_offset_hours:g} "
            "price_source=binance_spot_aggTrades "
            f"provenance={dataset_provenance or 'any'} "
            f"engine={'historical_compatible' if historical_compatible else 'live_v4'}"
        )

        for offset_index in range(windows):
            window_end = anchor_end - window_delta * offset_index
            window_start = window_end - window_delta
            features = await repository.load_features(
                symbol=symbol,
                start=window_start,
                end=window_end,
                dataset_provenance=dataset_provenance or None,
            )

            if features:
                discovery_result = runner.run(features, [])
                prediction_windows = [
                    (prediction.created_at, prediction.expires_at)
                    for prediction in discovery_result.predictions
                ]
                exact_prices = (
                    await historical_client.fetch_price_points_for_windows(
                        symbol,
                        windows=prediction_windows,
                    )
                    if prediction_windows
                    else []
                )
                result = runner.run(features, exact_prices)
            else:
                exact_prices = []
                result = ReplayResult(
                    decisions=(),
                    predictions=(),
                    evaluations=(),
                    open_predictions=(),
                )

            print(
                f"WINDOW {offset_index + 1} "
                f"start={window_start.isoformat()} "
                f"end={window_end.isoformat()} "
                f"features={len(features)} "
                f"predictions={len(result.predictions)} "
                f"exact_points={len(exact_prices)}"
            )

            for gate in frozen_gates:
                gated_result = filter_replay_result(
                    result,
                    gate,
                    features,
                )
                rows = build_barrier_sweep(
                    gated_result,
                    exact_prices,
                    round_trip_cost_pct=round_trip_cost_pct,
                    take_profit_grid=(gate.frozen_take_profit_pct,),
                    stop_loss_grid=(gate.frozen_stop_loss_pct,),
                )
                row = rows[0] if rows else None
                candidate_windows.append(
                    CandidateWindowResult(
                        window_index=offset_index + 1,
                        gate=gate,
                        result=gated_result,
                        price_points=tuple(exact_prices),
                        row=row,
                        has_feature_data=bool(features),
                    )
                )

                if row is None:
                    print(
                        f"  {gate.name} n=0 "
                        f"tp={gate.frozen_take_profit_pct:.2f}% "
                        f"sl={gate.frozen_stop_loss_pct:.2f}%"
                    )
                    continue

                profit_factor = (
                    "n/a"
                    if row.profit_factor is None
                    else f"{row.profit_factor:.3f}"
                )
                print(
                    f"  {gate.name} "
                    f"n={row.samples} "
                    f"TP={row.take_profit} "
                    f"SL={row.stop_loss} "
                    f"NT={row.no_touch} "
                    f"gross_expectancy={row.pre_cost_expectancy_pct:+.4f}% "
                    f"cost={row.execution_cost_pct:.4f}% "
                    f"net_expectancy={row.expectancy_pct:+.4f}% "
                    f"profit_factor={profit_factor}"
                )

        leaderboard = build_candidate_leaderboard(
            candidate_windows,
            minimum_trades=minimum_trades,
            minimum_active_windows=minimum_active_windows,
            minimum_positive_window_ratio=minimum_positive_window_ratio,
            minimum_profit_factor=minimum_profit_factor,
            round_trip_cost_pct=round_trip_cost_pct,
        )

        print()
        print("CANDIDATE LEADERBOARD")
        for rank, row in enumerate(leaderboard, start=1):
            expectancy = (
                "n/a"
                if row.expectancy_pct is None
                else f"{row.expectancy_pct:+.4f}%"
            )
            profit_factor = (
                "n/a"
                if row.profit_factor is None
                else f"{row.profit_factor:.3f}"
            )
            robustness_rate = (
                "n/a"
                if row.robustness_positive_expectancy_rate is None
                else f"{row.robustness_positive_expectancy_rate * Decimal('100'):.1f}%"
            )
            robustness_p05 = (
                "n/a"
                if row.robustness_p05_expectancy_pct is None
                else f"{row.robustness_p05_expectancy_pct:+.4f}%"
            )
            reasons = ",".join(row.reasons) or "none"
            status = "PROMOTE_TO_PAPER_V5" if row.promotion_ready else "HOLD"
            print(
                f"{rank}. {row.candidate_name} "
                f"windows={row.windows_tested} "
                f"data_windows={row.data_windows} "
                f"active={row.active_windows} "
                f"positive={row.positive_windows} "
                f"trades={row.trades} "
                f"net_expectancy={expectancy} "
                f"profit_factor={profit_factor} "
                f"robust={row.robustness_passed} "
                f"independent_samples={row.robustness_independent_samples} "
                f"bootstrap_positive={robustness_rate} "
                f"bootstrap_p05={robustness_p05} "
                f"status={status} "
                f"reasons={reasons}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run(
            symbol=args.symbol,
            window_hours=args.window_hours,
            windows=args.windows,
            end_offset_hours=args.end_offset_hours,
            minimum_trades=args.minimum_trades,
            minimum_active_windows=args.minimum_active_windows,
            minimum_positive_window_ratio=args.minimum_positive_window_ratio,
            minimum_profit_factor=args.minimum_profit_factor,
            end_at=args.end_at,
            dataset_provenance=args.dataset_provenance,
            historical_compatible=args.historical_compatible,
        )
    )


if __name__ == "__main__":
    main()
