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
from crypto_signal_engine.predictions import CompositePredictionEngine
from crypto_signal_engine.replay import (
    CANDIDATE_GATES,
    CandidateWindowResult,
    ReplayRunner,
    build_barrier_sweep,
    build_candidate_leaderboard,
    filter_replay_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run non-overlapping exact replay windows and rank frozen candidates."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--window-hours", type=float, default=12.0)
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--end-offset-hours", type=float, default=0.0)
    parser.add_argument("--minimum-trades", type=int, default=30)
    parser.add_argument("--minimum-active-windows", type=int, default=3)
    return parser.parse_args()


async def run(
    *,
    symbol: str,
    window_hours: float,
    windows: int,
    end_offset_hours: float,
    minimum_trades: int,
    minimum_active_windows: int,
) -> None:
    if window_hours <= 0:
        raise ValueError("window-hours must be positive")
    if windows <= 0:
        raise ValueError("windows must be positive")
    if end_offset_hours < 0:
        raise ValueError("end-offset-hours cannot be negative")

    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)
    round_trip_cost_pct = Decimal("2") * (
        settings.paper_fee_pct_per_side
        + settings.paper_slippage_pct_per_side
    )

    frozen_gates = tuple(
        gate
        for gate in CANDIDATE_GATES
        if gate.frozen_take_profit_pct is not None
        and gate.frozen_stop_loss_pct is not None
    )

    try:
        repository = ReplayDataRepository(create_session_factory(engine))
        runner = ReplayRunner(
            prediction_engine=CompositePredictionEngine(
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )
        client = BinanceSpotHistoricalTradeClient()
        all_windows: list[CandidateWindowResult] = []
        now = datetime.now(UTC)

        print(
            "WALK_FORWARD "
            f"symbol={symbol.upper()} windows={windows} "
            f"window_hours={window_hours:g} "
            f"end_offset_hours={end_offset_hours:g} "
            "price_source=binance_spot_aggTrades"
        )

        for index in range(windows):
            end = now - timedelta(
                hours=end_offset_hours + index * window_hours
            )
            start = end - timedelta(hours=window_hours)
            features = await repository.load_features(
                symbol=symbol,
                start=start,
                end=end,
            )

            print()
            print(
                f"WINDOW {index + 1}/{windows} "
                f"start={start.isoformat()} end={end.isoformat()} "
                f"features={len(features)}"
            )

            if not features:
                for gate in frozen_gates:
                    all_windows.append(
                        CandidateWindowResult(
                            window_index=index,
                            gate=gate,
                            result=runner.run([], []),
                            price_points=(),
                            row=None,
                        )
                    )
                print("  no feature data")
                continue

            discovery = runner.run(features, [])
            prediction_windows = [
                (prediction.created_at, prediction.expires_at)
                for prediction in discovery.predictions
            ]
            exact_prices = await client.fetch_price_points_for_windows(
                symbol,
                windows=prediction_windows,
            )
            exact_result = runner.run(features, exact_prices)

            print(
                f"  predictions={len(exact_result.predictions)} "
                f"exact_price_points={len(exact_prices)}"
            )

            for gate in frozen_gates:
                gated = filter_replay_result(
                    exact_result,
                    gate,
                    features,
                )
                rows = build_barrier_sweep(
                    gated,
                    exact_prices,
                    round_trip_cost_pct=round_trip_cost_pct,
                    take_profit_grid=(gate.frozen_take_profit_pct,),
                    stop_loss_grid=(gate.frozen_stop_loss_pct,),
                )
                row = rows[0] if rows else None
                all_windows.append(
                    CandidateWindowResult(
                        window_index=index,
                        gate=gate,
                        result=gated,
                        price_points=tuple(exact_prices),
                        row=row,
                    )
                )

                if row is None:
                    print(f"  {gate.name} n=0")
                    continue
                pf = (
                    "n/a"
                    if row.profit_factor is None
                    else f"{row.profit_factor:.3f}"
                )
                print(
                    f"  {gate.name} n={row.samples} "
                    f"TP={row.take_profit} SL={row.stop_loss} "
                    f"NT={row.no_touch} "
                    f"expectancy={row.expectancy_pct:+.4f}% "
                    f"profit_factor={pf}"
                )

        leaderboard = build_candidate_leaderboard(
            all_windows,
            minimum_trades=minimum_trades,
            minimum_active_windows=minimum_active_windows,
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
            pf = (
                "n/a"
                if row.profit_factor is None
                else f"{row.profit_factor:.3f}"
            )
            status = "PAPER_V5" if row.promotion_ready else "HOLD"
            print(
                f"{rank}. {row.candidate_name} "
                f"windows={row.windows_tested} "
                f"active={row.active_windows} "
                f"positive={row.positive_windows} "
                f"trades={row.trades} "
                f"expectancy={expectancy} "
                f"profit_factor={pf} "
                f"promotion={status}"
            )
            if row.reasons:
                print("   reasons=" + ",".join(row.reasons))
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
        )
    )


if __name__ == "__main__":
    main()
