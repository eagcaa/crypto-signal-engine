from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class PaperPositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class PaperRiskConfig:
    starting_equity: Decimal = Decimal("10000")
    risk_per_trade_pct: Decimal = Decimal("0.50")
    max_notional_pct: Decimal = Decimal("10")
    max_open_positions: int = 1
    stop_loss_pct: Decimal = Decimal("0.30")
    max_drawdown_pct: Decimal = Decimal("5.00")
    max_consecutive_losses: int = 5


@dataclass(slots=True)
class PaperPosition:
    id: UUID
    prediction_id: UUID
    symbol: str
    horizon_seconds: int
    direction: str
    opened_at: datetime
    entry_price: Decimal
    notional: Decimal
    quantity: Decimal
    model_name: str = "unknown"
    status: PaperPositionStatus = PaperPositionStatus.OPEN
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    return_pct: Decimal | None = None
    pnl: Decimal | None = None
    close_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    equity: Decimal
    realized_pnl: Decimal
    open_positions: int
    closed_positions: int
    invalidated_positions: int
    wins: int
    losses: int
    win_rate: Decimal | None
    peak_equity: Decimal
    drawdown_pct: Decimal
    max_drawdown_pct: Decimal
    consecutive_losses: int
    trading_halted: bool
    halt_reason: str | None
