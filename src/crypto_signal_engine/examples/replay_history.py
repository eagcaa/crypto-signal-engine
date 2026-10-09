import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import (
    CalibrationArtifact,
    ReplayCalibrator,
    save_calibration_artifact,
)
from crypto_signal_engine.collectors.binance import (
    BinanceSpotHistoricalTradeClient,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.db.replay_repository import ReplayDataRepository
from crypto_signal_engine.paper import (
    PaperRiskConfig,
    build_paper_performance_report,
    simulate_replay_broker,
    validate_paper_performance,
)
from crypto_signal_engine.predictions import CompositePredictionEngine
from crypto_signal_engine.replay import (
    ReplayRunner,
    build_barrier_sweep,
    build_excursion_stats,
    build_regime_excursion_stats,
    build_score_excursion_stats,
    compare_replay_reports,
    top_barrier_sweep_rows,
)
from crypto_signal_engine.replay.report import build_replay_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay persisted research features through the live V4 engine."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument(
        "--min-calibration-samples",
        type=int,
        default=30,
        help="Minimum evaluated samples required before exposing confidence.",
    )
    parser.add_argument(
        "--write-calibration",
        default="",
        help="Write calibration buckets to this JSON path.",
    )
    parser.add_argument(
        "--compare-price-sources",
        action="store_true",
        help="Run both sampled and Binance aggTrade replay and print deltas.",
    )
    parser.add_argument(
        "--exact-binance-trades",
        action="store_true",
        help=(
            "Use Binance spot aggTrades for first-touch evaluation instead "
            "of sampled persisted market snapshots."
        ),
    )
    return parser.parse_args()


def format_rate(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def validate_options(
    *,
    exact_binance_trades: bool,
    write_calibration_path: str,
) -> None:
    if write_calibration_path and not exact_binance_trades:
        raise ValueError(
            "Calibration artifacts require --exact-binance-trades "
            "to avoid sampled first-touch labels."
        )


async def run(
    symbol: str,
    hours: float,
    *,
    exact_binance_trades: bool = False,
    compare_price_sources: bool = False,
    minimum_calibration_samples: int = 30,
    write_calibration_path: str = "",
) -> None:
    validate_options(
        exact_binance_trades=exact_binance_trades,
        write_calibration_path=write_calibration_path,
    )

    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        session_factory = create_session_factory(engine)
        repository = ReplayDataRepository(session_factory)

        end = datetime.now(UTC)
        start = end - timedelta(hours=hours)

        features = await repository.load_features(
            symbol=symbol,
            start=start,
            end=end,
        )

        sampled_prices = await repository.load_price_points(
            symbol=symbol,
            start=start,
            end=end,
        )

        runner = ReplayRunner(
            prediction_engine=CompositePredictionEngine(
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )

        exact_prices = None
        if exact_binance_trades or compare_price_sources:
            discovery_result = runner.run(features, [])
            prediction_windows = [
                (prediction.created_at, prediction.expires_at)
                for prediction in discovery_result.predictions
            ]
            print(
                "EXACT FETCH "
                f"prediction_windows={len(prediction_windows)} "
                "mode=prediction-only"
            )
            exact_prices = (
                await BinanceSpotHistoricalTradeClient()
                .fetch_price_points_for_windows(
                    symbol,
                    windows=prediction_windows,
                )
            )

        if exact_binance_trades:
            prices = exact_prices or []
            price_source = "binance_spot_aggTrades"
        else:
            prices = sampled_prices
            price_source = "persisted_market_snapshots"

        print(
            f"REPLAY symbol={symbol.upper()} "
            f"start={start.isoformat()} end={end.isoformat()}"
        )
        print(
            f"loaded features={len(features)} "
            f"price_points={len(prices)} "
            f"price_source={price_source}"
        )

        if not features:
            print("No research feature snapshots found for the selected period.")
            return
        if (
            (exact_binance_trades or compare_price_sources)
            and not exact_prices
        ):
            print(
                "No exact Binance trade points found for prediction windows."
            )
            return
        if not prices:
            print("No market price snapshots found for the selected period.")
            return

        result = runner.run(features, prices)
        report = build_replay_report(result, features)

        comparison_rows = ()
        if compare_price_sources:
            exact_result = runner.run(features, exact_prices or [])
            exact_report = build_replay_report(exact_result, features)
            sampled_result = (
                result
                if not exact_binance_trades
                else runner.run(features, sampled_prices)
            )
            sampled_report = (
                report
                if not exact_binance_trades
                else build_replay_report(sampled_result, features)
            )
            comparison_rows = compare_replay_reports(
                sampled_report,
                exact_report,
            )
        calibration = ReplayCalibrator(
            minimum_samples=minimum_calibration_samples
        ).build(report)

        excursion_price_points = (
            exact_prices
            if exact_binance_trades
            else prices
        )
        excursion_result = (
            result
            if exact_binance_trades
            else (
                runner.run(features, exact_prices or [])
                if compare_price_sources
                else result
            )
        )
        excursion_stats = build_excursion_stats(
            excursion_result,
            list(excursion_price_points or []),
            round_trip_cost_pct=Decimal("2") * (
                settings.paper_fee_pct_per_side
                + settings.paper_slippage_pct_per_side
            ),
        )

        round_trip_cost_pct = Decimal("2") * (
            settings.paper_fee_pct_per_side
            + settings.paper_slippage_pct_per_side
        )
        score_excursion_stats = build_score_excursion_stats(
            excursion_result,
            list(excursion_price_points or []),
            round_trip_cost_pct=round_trip_cost_pct,
        )
        regime_excursion_stats = build_regime_excursion_stats(
            excursion_result,
            features,
            list(excursion_price_points or []),
            round_trip_cost_pct=round_trip_cost_pct,
        )

        barrier_sweep_rows = build_barrier_sweep(
            excursion_result,
            list(excursion_price_points or []),
            round_trip_cost_pct=round_trip_cost_pct,
        )
        top_barriers = top_barrier_sweep_rows(
            barrier_sweep_rows,
            per_group=5,
        )

        if write_calibration_path:
            artifact = CalibrationArtifact(
                schema_version=1,
                generated_at=datetime.now(UTC),
                symbol=symbol.upper(),
                start=start,
                end=end,
                price_source=price_source,
                minimum_samples=minimum_calibration_samples,
                buckets=calibration,
            )
            save_calibration_artifact(
                write_calibration_path,
                artifact,
            )
            print(
                "CALIBRATION_ARTIFACT "
                f"path={write_calibration_path} "
                f"buckets={len(calibration)} "
                f"source={price_source} "
                f"generated_at={artifact.generated_at.isoformat()}"
            )
        paper_broker = simulate_replay_broker(
            result,
            risk_config=PaperRiskConfig(
                starting_equity=settings.paper_starting_equity,
                risk_per_trade_pct=settings.paper_risk_per_trade_pct,
                max_notional_pct=settings.paper_max_notional_pct,
                max_open_positions=settings.paper_max_open_positions,
                max_drawdown_pct=settings.paper_max_drawdown_pct,
                max_consecutive_losses=settings.paper_max_consecutive_losses,
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            ),
        )

        print()
        if exact_binance_trades:
            print(
                "NOTE: first-touch evaluation uses historical Binance spot "
                "aggregate trades."
            )
        else:
            print("NOTE: persisted market snapshots are sampled, so first-touch")
            print(
                "results are approximate. Use --exact-binance-trades "
                "for trade-level evaluation."
            )
        print()

        for horizon, stats in report.by_horizon.items():
            print(
                f"{horizon // 60}m "
                f"predictions={stats.predictions} "
                f"TP={stats.take_profit} "
                f"SL={stats.stop_loss} "
                f"no_touch={stats.expired_no_touch} "
                f"no_data={stats.expired_without_data} "
                f"TP_rate={format_rate(stats.tp_rate)}"
            )

        if comparison_rows:
            print()
            print("SAMPLED VS EXACT FIRST-TOUCH")
            for row in comparison_rows:
                print(
                    f"{row.horizon_seconds // 60}m "
                    f"sampled=TP:{row.sampled_take_profit}/"
                    f"SL:{row.sampled_stop_loss}/"
                    f"NT:{row.sampled_no_touch} "
                    f"exact=TP:{row.exact_take_profit}/"
                    f"SL:{row.exact_stop_loss}/"
                    f"NT:{row.exact_no_touch} "
                    f"delta=TP:{row.take_profit_delta:+d}/"
                    f"SL:{row.stop_loss_delta:+d}/"
                    f"NT:{row.no_touch_delta:+d}"
                )

        print()
        print("MFE/MAE (full prediction horizon; exact when available)")
        for item in excursion_stats:
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"n={item.samples} "
                f"median_mfe={item.median_mfe_pct:.4f}% "
                f"p75_mfe={item.p75_mfe_pct:.4f}% "
                f"median_mae={item.median_mae_pct:.4f}% "
                f"p90_mae={item.p90_mae_pct:.4f}% "
                f"cost_clear={item.cost_clear_rate_pct:.2f}%"
            )

        print()
        print("MFE/MAE BY SCORE")
        for item in score_excursion_stats:
            upper = (
                f"{item.upper_bound:.2f}"
                if item.upper_bound is not None
                else "+"
            )
            label = (
                f"{item.lower_bound:.2f}-{upper}"
                if item.upper_bound is not None
                else f"{item.lower_bound:.2f}+"
            )
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"score={label} "
                f"n={item.samples} "
                f"median_mfe={item.median_mfe_pct:.4f}% "
                f"p75_mfe={item.p75_mfe_pct:.4f}% "
                f"median_mae={item.median_mae_pct:.4f}% "
                f"p90_mae={item.p90_mae_pct:.4f}% "
                f"cost_clear={item.cost_clear_rate_pct:.2f}%"
            )

        print()
        print("MFE/MAE BY REGIME")
        for item in regime_excursion_stats:
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"{item.trend_regime}+{item.volatility_regime} "
                f"n={item.samples} "
                f"median_mfe={item.median_mfe_pct:.4f}% "
                f"p75_mfe={item.p75_mfe_pct:.4f}% "
                f"median_mae={item.median_mae_pct:.4f}% "
                f"p90_mae={item.p90_mae_pct:.4f}% "
                f"cost_clear={item.cost_clear_rate_pct:.2f}%"
            )

        print()
        print("BARRIER SWEEP (in-sample research only)")
        for item in top_barriers:
            profit_factor = (
                "n/a"
                if item.profit_factor is None
                else f"{item.profit_factor:.3f}"
            )
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"tp={item.take_profit_pct:.2f}% "
                f"sl={item.stop_loss_pct:.2f}% "
                f"n={item.samples} "
                f"TP={item.take_profit} "
                f"SL={item.stop_loss} "
                f"NT={item.no_touch} "
                f"expectancy={item.expectancy_pct:+.4f}% "
                f"profit_factor={profit_factor}"
            )

        print()
        print("SCORE BINS (observed TP rate, not calibrated confidence)")
        for item in report.by_score_bin:
            upper = (
                f"{item.upper_bound:.2f}"
                if item.upper_bound is not None
                else "+"
            )
            label = (
                f"{item.lower_bound:.2f}-{upper}"
                if item.upper_bound is not None
                else f"{item.lower_bound:.2f}+"
            )
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"model={item.model_name} "
                f"score={label} "
                f"n={item.stats.predictions} "
                f"TP={item.stats.take_profit} "
                f"SL={item.stats.stop_loss} "
                f"no_touch={item.stats.expired_no_touch} "
                f"TP_rate={format_rate(item.stats.tp_rate)}"
            )

        print()
        print("CALIBRATION")
        for bucket in calibration:
            upper = (
                f"{bucket.upper_bound:.2f}"
                if bucket.upper_bound is not None
                else "+"
            )
            label = (
                f"{bucket.lower_bound:.2f}-{upper}"
                if bucket.upper_bound is not None
                else f"{bucket.lower_bound:.2f}+"
            )
            observed = format_rate(bucket.observed_tp_rate)
            confidence = (
                "not_ready"
                if bucket.calibrated_confidence is None
                else format_rate(bucket.calibrated_confidence)
            )
            print(
                f"{bucket.horizon_seconds // 60}m "
                f"{bucket.direction.upper()} "
                f"model={bucket.model_name} "
                f"score={label} "
                f"n={bucket.samples} "
                f"observed={observed} "
                f"confidence={confidence}"
            )

        print()
        print("REGIMES")
        for item in report.by_regime:
            if item.stats.predictions == 0:
                continue
            print(
                f"{item.horizon_seconds // 60}m "
                f"{item.direction.upper()} "
                f"{item.trend_regime}+{item.volatility_regime} "
                f"n={item.stats.predictions} "
                f"TP={item.stats.take_profit} "
                f"SL={item.stats.stop_loss} "
                f"no_touch={item.stats.expired_no_touch} "
                f"TP_rate={format_rate(item.stats.tp_rate)}"
            )

        paper = paper_broker.snapshot()
        paper_report = build_paper_performance_report(
            list(paper_broker.positions)
        )
        paper_validation = validate_paper_performance(
            paper,
            paper_report,
        )

        print()
        print("PAPER ACCOUNT")
        print(
            f"equity={paper.equity:.2f} "
            f"realized_pnl={paper.realized_pnl:+.2f} "
            f"closed={paper.closed_positions} "
            f"invalidated={paper.invalidated_positions} "
            f"wins={paper.wins} "
            f"losses={paper.losses} "
            f"win_rate={format_rate(paper.win_rate)} "
            f"drawdown={paper.drawdown_pct:.2f}% "
            f"max_drawdown={paper.max_drawdown_pct:.2f}% "
            f"consecutive_losses={paper.consecutive_losses} "
            f"halted={paper.trading_halted} "
            f"halt_reason={paper.halt_reason} "
            f"open={paper.open_positions}"
        )

        performance = paper_report.overall
        profit_factor = (
            "n/a"
            if performance.profit_factor is None
            else f"{performance.profit_factor:.3f}"
        )
        expectancy = (
            "n/a"
            if performance.expectancy is None
            else f"{performance.expectancy:+.4f}"
        )
        avg_return = format_rate(performance.average_return_pct)

        print(
            "PAPER PERFORMANCE "
            f"trades={performance.trades} "
            f"net_pnl={performance.net_pnl:+.2f} "
            f"gross_profit={performance.gross_profit:+.2f} "
            f"gross_loss={performance.gross_loss:+.2f} "
            f"pre_cost_pnl={performance.gross_pnl_before_costs:+.2f} "
            f"execution_costs={performance.execution_costs:.2f} "
            f"profit_factor={profit_factor} "
            f"expectancy={expectancy} "
            f"avg_return={avg_return}"
        )

        print()
        print("PAPER BY HORIZON/DIRECTION")
        for group in paper_report.by_horizon_direction:
            stats = group.stats
            group_profit_factor = (
                "n/a"
                if stats.profit_factor is None
                else f"{stats.profit_factor:.3f}"
            )
            group_expectancy = (
                "n/a"
                if stats.expectancy is None
                else f"{stats.expectancy:+.4f}"
            )
            print(
                f"{group.horizon_seconds // 60}m "
                f"{group.direction.upper()} "
                f"trades={stats.trades} "
                f"wins={stats.wins} "
                f"losses={stats.losses} "
                f"win_rate={format_rate(stats.win_rate)} "
                f"net_pnl={stats.net_pnl:+.2f} "
                f"execution_costs={stats.execution_costs:.2f} "
                f"profit_factor={group_profit_factor} "
                f"expectancy={group_expectancy} "
                f"avg_return={format_rate(stats.average_return_pct)}"
            )

        print()
        print(
            "PAPER VALIDATION "
            f"passed={paper_validation.passed} "
            f"reasons={','.join(paper_validation.reasons) or 'none'}"
        )

        print()
        print(
            f"no_trade_decisions={result.no_trade_count} "
            f"open_predictions={len(result.open_predictions)}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run(
            args.symbol,
            args.hours,
            exact_binance_trades=args.exact_binance_trades,
            compare_price_sources=args.compare_price_sources,
            minimum_calibration_samples=args.min_calibration_samples,
            write_calibration_path=args.write_calibration,
        )
    )


if __name__ == "__main__":
    main()
