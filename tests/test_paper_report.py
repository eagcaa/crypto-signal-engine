from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.paper.models import PaperPosition, PaperPositionStatus
from crypto_signal_engine.paper.report import build_paper_performance_report


def make_closed(
    *,
    horizon: int,
    direction: str,
    pnl: str,
    return_pct: str,
) -> PaperPosition:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return PaperPosition(
        id=uuid4(),
        prediction_id=uuid4(),
        symbol="BTCUSDT",
        horizon_seconds=horizon,
        direction=direction,
        opened_at=now,
        entry_price=Decimal("100"),
        notional=Decimal("1000"),
        quantity=Decimal("10"),
        model_name=(
            "composite_rules_v3_5m"
            if horizon == 300
            else "composite_rules_v3_15m"
        ),
        status=PaperPositionStatus.CLOSED,
        closed_at=now,
        exit_price=Decimal("100"),
        return_pct=Decimal(return_pct),
        pnl=Decimal(pnl),
        close_reason="test",
    )


def test_paper_report_calculates_profit_factor_and_expectancy() -> None:
    positions = [
        make_closed(
            horizon=300,
            direction="long",
            pnl="6",
            return_pct="0.60",
        ),
        make_closed(
            horizon=300,
            direction="long",
            pnl="-3",
            return_pct="-0.30",
        ),
        make_closed(
            horizon=900,
            direction="short",
            pnl="6",
            return_pct="0.60",
        ),
    ]

    report = build_paper_performance_report(positions)

    assert report.overall.trades == 3
    assert report.overall.wins == 2
    assert report.overall.losses == 1
    assert report.overall.gross_profit == Decimal("12")
    assert report.overall.gross_loss == Decimal("-3")
    assert report.overall.net_pnl == Decimal("9")
    assert report.overall.profit_factor == Decimal("4")
    assert report.overall.expectancy == Decimal("3")
    assert report.overall.win_rate == Decimal(2) / Decimal(3) * Decimal("100")
    assert report.overall.average_return_pct == Decimal("0.30")


def test_paper_report_groups_by_horizon_and_direction() -> None:
    report = build_paper_performance_report(
        [
            make_closed(
                horizon=300,
                direction="long",
                pnl="6",
                return_pct="0.60",
            ),
            make_closed(
                horizon=300,
                direction="short",
                pnl="-3",
                return_pct="-0.30",
            ),
        ]
    )

    assert len(report.by_horizon_direction) == 2
    long_group = next(
        group
        for group in report.by_horizon_direction
        if group.direction == "long"
    )
    assert long_group.horizon_seconds == 300
    assert long_group.stats.trades == 1
    assert long_group.stats.net_pnl == Decimal("6")



def test_paper_report_can_filter_old_model_positions() -> None:
    current = make_closed(
        horizon=300,
        direction="long",
        pnl="6",
        return_pct="0.60",
    )
    old = make_closed(
        horizon=300,
        direction="long",
        pnl="-3",
        return_pct="-0.30",
    )
    old.model_name = "composite_rules_v2_5m"

    report = build_paper_performance_report(
        [current, old],
        model_names=("composite_rules_v3_5m",),
    )

    assert report.overall.trades == 1
    assert report.overall.net_pnl == Decimal("6")
