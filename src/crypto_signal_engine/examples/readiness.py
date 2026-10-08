import asyncio

from crypto_signal_engine.calibration import load_calibration
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
from crypto_signal_engine.readiness import evaluate_readiness


async def run() -> int:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        repository = PaperPositionRepository(
            create_session_factory(engine)
        )
        positions = await repository.load_all()

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
        broker.restore(positions)
        account = broker.snapshot()
        paper_report = build_paper_performance_report(
            list(broker.positions)
        )
        paper_validation = validate_paper_performance(
            account,
            paper_report,
        )

        calibration = load_calibration(settings.calibration_file)
        result = evaluate_readiness(
            calibration,
            paper_validation,
        )

        ready_buckets = sum(
            1
            for bucket in calibration
            if bucket.calibrated_confidence is not None
        )

        print("READINESS")
        print(
            f"ready={result.ready} "
            f"calibration_buckets={len(calibration)} "
            f"calibration_ready={ready_buckets} "
            f"paper_trades={paper_report.overall.trades} "
            f"paper_validation={paper_validation.passed}"
        )

        if result.reasons:
            for reason in result.reasons:
                print(f"- {reason}")
        else:
            print("- all configured research gates passed")

        return 0 if result.ready else 1
    finally:
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
