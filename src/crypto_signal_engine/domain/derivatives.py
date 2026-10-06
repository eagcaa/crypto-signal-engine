from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from crypto_signal_engine.domain.models import Exchange


class LiquidatedPositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class ExchangeDerivativesSnapshot:
    exchange: Exchange
    symbol: str
    timestamp: datetime
    open_interest: Decimal | None
    open_interest_value_usd: Decimal | None
    oi_change_5m_pct: Decimal | None
    oi_change_15m_pct: Decimal | None
    funding_rate: Decimal | None
    long_short_ratio: Decimal | None
    long_account_ratio: Decimal | None
    short_account_ratio: Decimal | None
    top_trader_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None
    taker_buy_volume: Decimal | None
    taker_sell_volume: Decimal | None


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    exchange: Exchange
    symbol: str
    event_time: datetime
    position_side: LiquidatedPositionSide
    price: Decimal
    quantity: Decimal

    @property
    def notional_usd(self) -> Decimal:
        return self.price * self.quantity
