from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal = Decimal("0")
    taker_buy_base_volume: Decimal = Decimal("0")
    taker_buy_quote_volume: Decimal = Decimal("0")
    trade_count: int = 0
    closed: bool = True
