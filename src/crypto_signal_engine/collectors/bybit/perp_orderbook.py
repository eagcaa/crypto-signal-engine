"""Read-only Bybit linear-perpetual REST orderbook (distinct from existing spot stream)."""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import aiohttp

from crypto_signal_engine.collectors.bybit.candles import SYMBOLS

D = Decimal


@dataclass(frozen=True)
class PerpOrderBook:
    symbol: str
    timestamp: datetime
    best_bid: Decimal
    best_ask: Decimal
    spread_bps: Decimal
    bid_notional: Decimal
    ask_notional: Decimal
    imbalance: Decimal
    pressure: str


def parse_perp_orderbook(payload: dict, symbol: str) -> PerpOrderBook:
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit orderbook error: {payload.get('retMsg')}")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("s") != symbol:
        raise ValueError("Invalid orderbook symbol/result")

    def levels(key):
        raw = result.get(key)
        if not isinstance(raw, list) or not raw:
            raise ValueError("Missing orderbook levels")
        values = [(D(str(row[0])), D(str(row[1]))) for row in raw]
        if any(not p.is_finite() or not q.is_finite() or p <= 0 or q <= 0 for p, q in values):
            raise ValueError("Invalid orderbook level")
        return values

    bids, asks = levels("b"), levels("a")
    bid, ask = max(p for p, _ in bids), min(p for p, _ in asks)
    if bid >= ask:
        raise ValueError("Crossed orderbook")
    bid_usdt = sum((p * q for p, q in bids), D(0))
    ask_usdt = sum((p * q for p, q in asks), D(0))
    imbalance = (bid_usdt - ask_usdt) / (bid_usdt + ask_usdt)
    pressure = ("BUY_PRESSURE" if imbalance >= D("0.15") else
                "SELL_PRESSURE" if imbalance <= D("-0.15") else "BALANCED")
    timestamp_ms = result.get("ts") or payload.get("time")
    if timestamp_ms is None:
        raise ValueError("Missing orderbook timestamp")
    return PerpOrderBook(symbol, datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC),
                         bid, ask, (ask - bid) / ((ask + bid) / 2) * 10000,
                         bid_usdt, ask_usdt, imbalance, pressure)


class BybitPerpOrderBookClient:
    URL = "https://api.bybit.com/v5/market/orderbook"

    def __init__(self, timeout_seconds: float = 10):
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch(self, symbol: str, *, limit: int = 50) -> PerpOrderBook:
        symbol = symbol.upper()
        if symbol not in SYMBOLS:
            raise ValueError("Unsupported symbol")
        if limit not in (1, 50, 200, 500):
            raise ValueError("Unsupported Bybit linear depth")
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(self.URL, params={
                "category": "linear", "symbol": symbol, "limit": str(limit)
            }) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"Bybit orderbook HTTP {response.status}")
        return parse_perp_orderbook(payload, symbol)
