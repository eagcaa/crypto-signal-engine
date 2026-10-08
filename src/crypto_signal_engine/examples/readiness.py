import asyncio
from datetime import UTC, datetime, timedelta

from crypto_signal_engine.calibration import (
    load_calibration,
    load_calibration_artifact,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    MarketSnapshotRepository,
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
from crypto_signal_engine.readiness import evaluate_readiness


async def run() -> int:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        session_factory = create_session_factory(engine)
        repository = PaperPositionRepository(session_factory)
        market_repository = MarketSnapshotRepository(session_factory)
        positions = await repository.load_all()
        latest_market = await market_repository.latest_health("BTCUSDT")
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
        paper_report = build_paper_performance_report(
            list(broker.positions),
            model_names=current_model_names,
        )
        paper_validation = validate_paper_performance(
            account,
            paper_report,
        )

        calibration = load_calibration(settings.calibration_file)
        calibration_artifact = load_calibration_artifact(
            settings.calibration_file
        )
        result = evaluate_readiness(
            calibration,
            paper_validation,
            latest_market_timestamp=(
                latest_market.timestamp
                if latest_market is not None
                else None
            ),
            latest_market_quality=(
                latest_market.data_quality
                if latest_market is not None
                else None
            ),
            now=datetime.now(UTC),
            minimum_market_quality=settings.data_quality_alert_threshold,
            calibration_artifact=calibration_artifact,
            maximum_calibration_age=timedelta(
                hours=settings.calibration_max_age_hours
            ),
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
            f"calibration_source={calibration_artifact.price_source if calibration_artifact else 'missing'} "
            f"calibration_generated_at={calibration_artifact.generated_at if calibration_artifact else 'missing'} "
            f"paper_trades={paper_report.overall.trades} "
            f"paper_positions_current_model={len(current_positions)} "
            f"paper_positions_total={len(positions)} "
            f"paper_validation={paper_validation.passed} "
            f"market_quality={latest_market.data_quality if latest_market else 'missing'} "
            f"market_timestamp={latest_market.timestamp if latest_market else 'missing'}"
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
