from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.paper.models import PaperPosition, PaperPositionStatus


@dataclass(frozen=True, slots=True)
class PaperPerformanceStats:
    trades: int
    wins: int
    losses: int
    breakeven: int
    gross_profit: Decimal
    gross_loss: Decimal
    net_pnl: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    average_return_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class PaperPerformanceGroup:
    horizon_seconds: int
    direction: str
    stats: PaperPerformanceStats


@dataclass(frozen=True, slots=True)
class PaperPerformanceReport:
    overall: PaperPerformanceStats
    by_horizon_direction: tuple[PaperPerformanceGroup, ...]


def build_paper_performance_report(
    positions: tuple[PaperPosition, ...] | list[PaperPosition],
) -> PaperPerformanceReport:
    closed = [
        position
        for position in positions
        if position.status == PaperPositionStatus.CLOSED
    ]

    groups: dict[tuple[int, str], list[PaperPosition]] = defaultdict(list)
    for position in closed:
        groups[(position.horizon_seconds, position.direction)].append(position)

    return PaperPerformanceReport(
        overall=_stats(closed),
        by_horizon_direction=tuple(
            PaperPerformanceGroup(
                horizon_seconds=key[0],
                direction=key[1],
                stats=_stats(group_positions),
            )
            for key, group_positions in sorted(groups.items())
        ),
    )


def _stats(positions: list[PaperPosition]) -> PaperPerformanceStats:
    pnls = [position.pnl or Decimal("0") for position in positions]
    returns = [
        position.return_pct
        for position in positions
        if position.return_pct is not None
    ]

    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl < 0)
    breakeven = sum(1 for pnl in pnls if pnl == 0)

    gross_profit = sum(
        (pnl for pnl in pnls if pnl > 0),
        Decimal("0"),
    )
    gross_loss = sum(
        (pnl for pnl in pnls if pnl < 0),
        Decimal("0"),
    )
    net_pnl = gross_profit + gross_loss

    trades = len(positions)
    win_rate = (
        Decimal(wins) / Decimal(trades) * Decimal("100")
        if trades
        else None
    )

    profit_factor = None
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)

    expectancy = (
        net_pnl / Decimal(trades)
        if trades
        else None
    )

    average_return_pct = (
        sum(returns, Decimal("0")) / Decimal(len(returns))
        if returns
        else None
    )

    return PaperPerformanceStats(
        trades=trades,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_return_pct=average_return_pct,
    )
