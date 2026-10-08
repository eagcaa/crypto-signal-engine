import asyncio
from decimal import Decimal

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    PaperPositionRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.paper import (
    PaperBroker,
    PaperRiskConfig,
    build_paper_performance_report,
    validate_paper_performance,
)
from crypto_signal_engine.predictions import CompositePredictionEngine


def format_rate(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


async def run() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        repository = PaperPositionRepository(
            create_session_factory(engine)
        )
        positions = await repository.load_all()
        current_model_names = tuple(
            model_name
            for _, model_name
            in CompositePredictionEngine.current_model_names()
        )
        current_positions = [
            position
            for position in positions
            if position.model_name in current_model_names
        ]

        broker = PaperBroker(
            PaperRiskConfig(
                starting_equity=settings.paper_starting_equity,
                risk_per_trade_pct=settings.paper_risk_per_trade_pct,
                max_notional_pct=settings.paper_max_notional_pct,
                max_open_positions=settings.paper_max_open_positions,
                max_drawdown_pct=settings.paper_max_drawdown_pct,
                max_consecutive_losses=settings.paper_max_consecutive_losses,
            )
        )
        broker.restore(current_positions)

        account = broker.snapshot()
        report = build_paper_performance_report(
            list(broker.positions),
            model_names=current_model_names,
        )
        validation = validate_paper_performance(
            account,
            report,
        )

        overall = report.overall
        profit_factor = (
            "n/a"
            if overall.profit_factor is None
            else f"{overall.profit_factor:.3f}"
        )
        expectancy = (
            "n/a"
            if overall.expectancy is None
            else f"{overall.expectancy:+.4f}"
        )

        print("PAPER REPORT")
        print(
            f"positions_current_model={len(current_positions)} "
            f"positions_total={len(positions)} "
            f"closed={account.closed_positions} "
            f"open={account.open_positions} "
            f"invalidated={account.invalidated_positions}"
        )
        print(
            f"equity={account.equity:.2f} "
            f"realized_pnl={account.realized_pnl:+.2f} "
            f"wins={account.wins} "
            f"losses={account.losses} "
            f"win_rate={format_rate(account.win_rate)} "
            f"max_drawdown={account.max_drawdown_pct:.2f}%"
        )
        print(
            f"profit_factor={profit_factor} "
            f"expectancy={expectancy} "
            f"avg_return={format_rate(overall.average_return_pct)}"
        )

        print()
        print("BY HORIZON/DIRECTION")
        for group in report.by_horizon_direction:
            stats = group.stats
            group_pf = (
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
                f"win_rate={format_rate(stats.win_rate)} "
                f"net_pnl={stats.net_pnl:+.2f} "
                f"profit_factor={group_pf} "
                f"expectancy={group_expectancy}"
            )

        print()
        print(
            "PAPER VALIDATION "
            f"passed={validation.passed} "
            f"reasons={','.join(validation.reasons) or 'none'}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
