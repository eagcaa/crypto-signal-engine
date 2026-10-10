from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.examples.shadow_report import (
    ShadowOutcome,
    build_shadow_report,
)


def test_shadow_report_separates_gross_cost_and_net() -> None:
    started = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    report = build_shadow_report(
        [
            ShadowOutcome(
                created_at=started,
                outcome="take_profit",
                gross_return_pct=Decimal("0.50"),
            ),
            ShadowOutcome(
                created_at=started + timedelta(minutes=70),
                outcome="expired_no_touch",
                gross_return_pct=Decimal("0.10"),
            ),
            ShadowOutcome(
                created_at=started + timedelta(hours=3),
                outcome="stop_loss",
                gross_return_pct=Decimal("-0.20"),
            ),
        ],
        predictions=4,
        round_trip_cost_pct=Decimal("0.12"),
        independence_bucket_minutes=120,
    )

    assert report.predictions == 4
    assert report.closed == 3
    assert report.open == 1
    assert report.take_profit == 1
    assert report.stop_loss == 1
    assert report.no_touch == 1
    assert report.gross_expectancy_pct == Decimal("0.1333333333333333333333333333")
    assert report.net_expectancy_pct == Decimal("0.01333333333333333333333333333")
    assert report.execution_cost_pct == Decimal("0.12")
    assert report.independent_samples == 2
    assert report.profit_factor == Decimal("0.38") / Decimal("0.34")


def test_shadow_report_excludes_no_data_from_expectancy() -> None:
    started = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    report = build_shadow_report(
        [
            ShadowOutcome(
                created_at=started,
                outcome="expired_without_data",
                gross_return_pct=Decimal("0"),
            )
        ],
        predictions=1,
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert report.closed == 1
    assert report.no_data == 1
    assert report.gross_expectancy_pct is None
    assert report.net_expectancy_pct is None
    assert report.profit_factor is None
    assert report.independent_samples == 0
