import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.db.models import (
    PredictionEvaluationRow,
    PredictionRow,
)


SHADOW_MODEL = "shadow_composite_rules_v4_1h"
SHADOW_HORIZON_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    created_at: datetime
    outcome: str
    gross_return_pct: Decimal


@dataclass(frozen=True, slots=True)
class ShadowReport:
    predictions: int
    closed: int
    open: int
    take_profit: int
    stop_loss: int
    no_touch: int
    no_data: int
    gross_expectancy_pct: Decimal | None
    execution_cost_pct: Decimal
    net_expectancy_pct: Decimal | None
    profit_factor: Decimal | None
    independent_samples: int


def build_shadow_report(
    outcomes: list[ShadowOutcome],
    *,
    predictions: int,
    round_trip_cost_pct: Decimal,
    independence_bucket_minutes: int = 120,
) -> ShadowReport:
    evaluated = [
        item
        for item in outcomes
        if item.outcome != "expired_without_data"
    ]
    net_returns = [
        item.gross_return_pct - round_trip_cost_pct
        for item in evaluated
    ]

    gross_expectancy = (
        sum(
            (item.gross_return_pct for item in evaluated),
            Decimal("0"),
        )
        / Decimal(len(evaluated))
        if evaluated
        else None
    )
    net_expectancy = (
        sum(net_returns, Decimal("0")) / Decimal(len(net_returns))
        if net_returns
        else None
    )

    gross_profit = sum(
        (value for value in net_returns if value > 0),
        Decimal("0"),
    )
    gross_loss = -sum(
        (value for value in net_returns if value < 0),
        Decimal("0"),
    )
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else None
    )

    bucket_seconds = independence_bucket_minutes * 60
    independent_buckets = {
        int(item.created_at.timestamp()) // bucket_seconds
        for item in evaluated
    }

    closed = len(outcomes)
    return ShadowReport(
        predictions=predictions,
        closed=closed,
        open=max(0, predictions - closed),
        take_profit=sum(1 for item in outcomes if item.outcome == "take_profit"),
        stop_loss=sum(1 for item in outcomes if item.outcome == "stop_loss"),
        no_touch=sum(
            1 for item in outcomes if item.outcome == "expired_no_touch"
        ),
        no_data=sum(
            1 for item in outcomes if item.outcome == "expired_without_data"
        ),
        gross_expectancy_pct=gross_expectancy,
        execution_cost_pct=round_trip_cost_pct,
        net_expectancy_pct=net_expectancy,
        profit_factor=profit_factor,
        independent_samples=len(independent_buckets),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report persisted 1h shadow prediction economics."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--hours",
        type=float,
        default=168.0,
        help="Lookback window in hours. Default: 168 (7 days).",
    )
    return parser.parse_args()


def _fmt(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.4f}%" if signed else f"{value:.4f}%"


async def run(*, symbol: str, hours: float) -> None:
    if hours <= 0:
        raise ValueError("hours must be positive")

    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        session_factory = create_session_factory(engine)
        start = datetime.now(UTC) - timedelta(hours=hours)

        async with session_factory() as session:
            predictions_query = (
                select(PredictionRow)
                .where(
                    PredictionRow.symbol == symbol.upper(),
                    PredictionRow.horizon_seconds == SHADOW_HORIZON_SECONDS,
                    PredictionRow.model_name == SHADOW_MODEL,
                    PredictionRow.created_at >= start,
                )
                .order_by(PredictionRow.created_at.asc())
            )
            predictions = list(
                (await session.scalars(predictions_query)).all()
            )

            if not predictions:
                print(
                    "SHADOW 1H REPORT "
                    f"symbol={symbol.upper()} hours={hours:g} "
                    "predictions=0"
                )
                return

            prediction_ids = [item.id for item in predictions]
            evaluations_query = (
                select(PredictionEvaluationRow)
                .where(
                    PredictionEvaluationRow.prediction_id.in_(prediction_ids)
                )
            )
            evaluations = list(
                (await session.scalars(evaluations_query)).all()
            )
            prediction_by_id = {
                item.id: item for item in predictions
            }

            outcomes = [
                ShadowOutcome(
                    created_at=prediction_by_id[item.prediction_id].created_at,
                    outcome=item.outcome or "unresolved",
                    gross_return_pct=item.return_pct or Decimal("0"),
                )
                for item in evaluations
            ]

        round_trip_cost_pct = Decimal("2") * (
            settings.paper_fee_pct_per_side
            + settings.paper_slippage_pct_per_side
        )
        report = build_shadow_report(
            outcomes,
            predictions=len(predictions),
            round_trip_cost_pct=round_trip_cost_pct,
            independence_bucket_minutes=120,
        )

        profit_factor = (
            "n/a"
            if report.profit_factor is None
            else f"{report.profit_factor:.3f}"
        )
        print(
            "SHADOW 1H REPORT "
            f"symbol={symbol.upper()} "
            f"hours={hours:g} "
            f"model={SHADOW_MODEL}"
        )
        print(
            f"predictions={report.predictions} "
            f"closed={report.closed} "
            f"open={report.open} "
            f"TP={report.take_profit} "
            f"SL={report.stop_loss} "
            f"NT={report.no_touch} "
            f"no_data={report.no_data}"
        )
        print(
            f"gross_expectancy={_fmt(report.gross_expectancy_pct, signed=True)} "
            f"cost={report.execution_cost_pct:.4f}% "
            f"net_expectancy={_fmt(report.net_expectancy_pct, signed=True)} "
            f"profit_factor={profit_factor} "
            f"independent_samples_2h={report.independent_samples}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(run(symbol=args.symbol, hours=args.hours))


if __name__ == "__main__":
    main()
