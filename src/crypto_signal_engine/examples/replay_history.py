import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.calibration import ReplayCalibrator, save_calibration
from crypto_signal_engine.collectors.binance import (
    BinanceSpotHistoricalTradeClient,
)
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
from crypto_signal_engine.replay import ReplayRunner, compare_replay_reports
from crypto_signal_engine.replay.report import build_replay_report
from crypto_signal_engine.config.settings import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay persisted research features through the live V3 engine."
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

        exact_prices = None
        if exact_binance_trades or compare_price_sources:
            exact_prices = await BinanceSpotHistoricalTradeClient().fetch_price_points(
                symbol,
                start=start,
                end=end,
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
        if not prices:
            print("No market price snapshots found for the selected period.")
            return

        runner = ReplayRunner()
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

        if write_calibration_path:
            save_calibration(write_calibration_path, calibration)
            print(
                "CALIBRATION_ARTIFACT "
                f"path={write_calibration_path} "
                f"buckets={len(calibration)}"
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
