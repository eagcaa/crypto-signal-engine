import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
)
from crypto_signal_engine.replay import ReplayRunner
from crypto_signal_engine.replay.report import build_replay_report
from crypto_signal_engine.config.settings import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay persisted research features through the live V3 engine."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--hours", type=float, default=6.0)
    return parser.parse_args()


def format_rate(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


async def run(symbol: str, hours: float) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        session_factory = create_session_factory(engine)
        repository = ReplayDataRepository(session_factory)

        end = datetime.now(UTC)
        start = end - timedelta(hours=hours)

        features, prices = await asyncio.gather(
            repository.load_features(
                symbol=symbol,
                start=start,
                end=end,
            ),
            repository.load_price_points(
                symbol=symbol,
                start=start,
                end=end,
            ),
        )

        print(
            f"REPLAY symbol={symbol.upper()} "
            f"start={start.isoformat()} end={end.isoformat()}"
        )
        print(
            f"loaded features={len(features)} price_points={len(prices)}"
        )

        if not features:
            print("No research feature snapshots found for the selected period.")
            return
        if not prices:
            print("No market price snapshots found for the selected period.")
            return

        result = ReplayRunner().run(features, prices)
        report = build_replay_report(result, features)
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
        print("NOTE: persisted market snapshots are sampled, so first-touch")
        print("results are approximate until trade-level historical replay is added.")
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
                f"score={label} "
                f"n={item.stats.predictions} "
                f"TP={item.stats.take_profit} "
                f"SL={item.stats.stop_loss} "
                f"no_touch={item.stats.expired_no_touch} "
                f"TP_rate={format_rate(item.stats.tp_rate)}"
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
            f"no_trade_decisions={result.no_trade_count} "
            f"open_predictions={len(result.open_predictions)}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.symbol, args.hours))


if __name__ == "__main__":
    main()
