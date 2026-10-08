from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.paper.models import PaperAccountSnapshot
from crypto_signal_engine.paper.report import PaperPerformanceReport


@dataclass(frozen=True, slots=True)
class PaperValidationConfig:
    minimum_closed_trades: int = 30
    minimum_profit_factor: Decimal = Decimal("1.10")
    minimum_expectancy: Decimal = Decimal("0")
    maximum_drawdown_pct: Decimal = Decimal("5.00")


@dataclass(frozen=True, slots=True)
class PaperValidationResult:
    passed: bool
    reasons: tuple[str, ...]


def validate_paper_performance(
    account: PaperAccountSnapshot,
    report: PaperPerformanceReport,
    *,
    config: PaperValidationConfig | None = None,
) -> PaperValidationResult:
    """Apply transparent minimum research gates before any live-money pilot."""

    config = config or PaperValidationConfig()
    reasons: list[str] = []
    overall = report.overall

    if overall.trades < config.minimum_closed_trades:
        reasons.append(
            "insufficient_trades:"
            f"{overall.trades}/{config.minimum_closed_trades}"
        )

    if overall.profit_factor is None:
        reasons.append("profit_factor_unavailable")
    elif overall.profit_factor < config.minimum_profit_factor:
        reasons.append(
            "profit_factor_below_minimum:"
            f"{overall.profit_factor:.4f}/{config.minimum_profit_factor:.4f}"
        )

    if overall.expectancy is None:
        reasons.append("expectancy_unavailable")
    elif overall.expectancy <= config.minimum_expectancy:
        reasons.append(
            "expectancy_not_positive:"
            f"{overall.expectancy:.4f}"
        )

    if account.max_drawdown_pct > config.maximum_drawdown_pct:
        reasons.append(
            "drawdown_above_maximum:"
            f"{account.max_drawdown_pct:.4f}%/"
            f"{config.maximum_drawdown_pct:.4f}%"
        )

    if account.trading_halted:
        reasons.append(
            "paper_trading_halted:"
            f"{account.halt_reason or 'unknown'}"
        )

    return PaperValidationResult(
        passed=not reasons,
        reasons=tuple(reasons),
    )
