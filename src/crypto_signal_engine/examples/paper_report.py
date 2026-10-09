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
    CandidatePaperTracker,
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
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
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
            f"pre_cost_pnl={overall.gross_pnl_before_costs:+.2f} "
            f"execution_costs={overall.execution_costs:.2f} "
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
                f"execution_costs={stats.execution_costs:.2f} "
                f"profit_factor={group_pf} "
                f"expectancy={group_expectancy}"
            )

        print()
        print(
            "PAPER VALIDATION "
            f"passed={validation.passed} "
            f"reasons={','.join(validation.reasons) or 'none'}"
        )

        candidate_model_name = (
            f"{CandidatePaperTracker.MODEL_PREFIX}_"
            f"{settings.candidate_paper_name}"
        )
        candidate_positions = [
            position
            for position in positions
            if position.model_name == candidate_model_name
        ]
        candidate_broker = PaperBroker(
            PaperRiskConfig(
                starting_equity=settings.paper_starting_equity,
                risk_per_trade_pct=settings.paper_risk_per_trade_pct,
                max_notional_pct=settings.paper_max_notional_pct,
                max_open_positions=1,
                max_drawdown_pct=settings.paper_max_drawdown_pct,
                max_consecutive_losses=settings.paper_max_consecutive_losses,
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )
        candidate_broker.restore(candidate_positions)
        candidate_account = candidate_broker.snapshot()
        candidate_report = build_paper_performance_report(
            list(candidate_broker.positions),
            model_names=(candidate_model_name,),
        )
        candidate_stats = candidate_report.overall
        candidate_pf = (
            "n/a"
            if candidate_stats.profit_factor is None
            else f"{candidate_stats.profit_factor:.3f}"
        )
        candidate_expectancy = (
            "n/a"
            if candidate_stats.expectancy is None
            else f"{candidate_stats.expectancy:+.4f}"
        )

        print()
        print("CANDIDATE PAPER REPORT")
        print(
            f"candidate={settings.candidate_paper_name} "
            f"model={candidate_model_name} "
            f"positions={len(candidate_positions)} "
            f"closed={candidate_account.closed_positions} "
            f"open={candidate_account.open_positions} "
            f"wins={candidate_account.wins} "
            f"losses={candidate_account.losses} "
            f"win_rate={format_rate(candidate_account.win_rate)} "
            f"profit_factor={candidate_pf} "
            f"expectancy={candidate_expectancy} "
            f"realized_pnl={candidate_account.realized_pnl:+.2f} "
            f"max_drawdown={candidate_account.max_drawdown_pct:.2f}%"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
