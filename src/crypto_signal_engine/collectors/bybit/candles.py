"""Public Bybit linear-perpetual closed kline reader (no trading credentials)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp

from crypto_signal_engine.domain.candles import Candle

INTERVALS = {"1m": ("1", 1), "3m": ("3", 3), "5m": ("5", 5), "15m": ("15", 15)}
SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


class BybitLinearCandleClient:
    BASE_URL = "https://api.bybit.com/v5/market/kline"

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_closed(self, symbol: str, *, interval: str, limit: int = 150, now: datetime | None = None) -> list[Candle]:
        symbol = symbol.upper()
        if symbol not in SYMBOLS or interval not in INTERVALS:
            raise ValueError("Unsupported Bybit symbol or interval")
        if not 1 <= limit <= 999:
            raise ValueError("limit must be between 1 and 999")
        code, minutes = INTERVALS[interval]
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        params = {"category": "linear", "symbol": symbol, "interval": code, "limit": str(limit + 1)}
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(self.BASE_URL, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400 or payload.get("retCode") != 0:
                    raise RuntimeError(f"Bybit kline error HTTP {response.status}: {payload.get('retMsg')}")
        rows = payload.get("result", {}).get("list", [])
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected Bybit kline payload")
        candles = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            opened = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
            closed_at = opened + timedelta(minutes=minutes)
            if closed_at > now:
                continue
            candles.append(Candle(
                symbol=symbol, interval=interval, open_time=opened, close_time=closed_at,
                open=Decimal(row[1]), high=Decimal(row[2]), low=Decimal(row[3]),
                close=Decimal(row[4]), volume=Decimal(row[5]), quote_volume=Decimal(row[6]),
            ))
        return sorted({c.open_time: c for c in candles}.values(), key=lambda c: c.open_time)[-limit:]
