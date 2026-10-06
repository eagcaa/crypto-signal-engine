from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Exchange(StrEnum):
    BINANCE = "binance"
    BYBIT = "bybit"


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class TradeTick:
    exchange: Exchange
    market_type: MarketType
    symbol: str
    event_time: datetime
    price: Decimal
    quantity: Decimal
    side: TradeSide


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    exchange: Exchange
    market_type: MarketType
    symbol: str
    event_time: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
