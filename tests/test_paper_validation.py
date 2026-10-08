from decimal import Decimal

from crypto_signal_engine.paper.models import PaperAccountSnapshot
from crypto_signal_engine.paper.report import (
    PaperPerformanceReport,
    PaperPerformanceStats,
)
from crypto_signal_engine.paper.validation import (
    PaperValidationConfig,
    validate_paper_performance,
)


def make_account(
    *,
    max_drawdown: str = "2",
    halted: bool = False,
) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        equity=Decimal("10100"),
        realized_pnl=Decimal("100"),
        open_positions=0,
        closed_positions=40,
        invalidated_positions=0,
        wins=24,
        losses=16,
        win_rate=Decimal("60"),
        peak_equity=Decimal("10200"),
        drawdown_pct=Decimal("0.98"),
        max_drawdown_pct=Decimal(max_drawdown),
        consecutive_losses=0,
        trading_halted=halted,
        halt_reason="test_halt" if halted else None,
    )


def make_report(
    *,
    trades: int = 40,
    profit_factor: str = "1.50",
    expectancy: str = "2.50",
) -> PaperPerformanceReport:
    stats = PaperPerformanceStats(
        trades=trades,
        wins=24,
        losses=16,
        breakeven=0,
        gross_profit=Decimal("160"),
        gross_loss=Decimal("-60"),
        net_pnl=Decimal("100"),
        win_rate=Decimal("60"),
        profit_factor=Decimal(profit_factor),
        expectancy=Decimal(expectancy),
        average_return_pct=Decimal("0.10"),
    )
    return PaperPerformanceReport(
        overall=stats,
        by_horizon_direction=(),
    )


def test_validation_passes_when_all_gates_are_met() -> None:
    result = validate_paper_performance(
        make_account(),
        make_report(),
    )

    assert result.passed is True
    assert result.reasons == ()


def test_validation_reports_all_failed_gates() -> None:
    result = validate_paper_performance(
        make_account(max_drawdown="6", halted=True),
        make_report(
            trades=10,
            profit_factor="0.90",
            expectancy="-1",
        ),
        config=PaperValidationConfig(
            minimum_closed_trades=30,
            minimum_profit_factor=Decimal("1.10"),
            minimum_expectancy=Decimal("0"),
            maximum_drawdown_pct=Decimal("5"),
        ),
    )

    assert result.passed is False
    assert any(reason.startswith("insufficient_trades:") for reason in result.reasons)
    assert any(
        reason.startswith("profit_factor_below_minimum:")
        for reason in result.reasons
    )
    assert any(
        reason.startswith("expectancy_not_positive:")
        for reason in result.reasons
    )
    assert any(
        reason.startswith("drawdown_above_maximum:")
        for reason in result.reasons
    )
    assert any(
        reason.startswith("paper_trading_halted:")
        for reason in result.reasons
    )
