from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp

from crypto_signal_engine.domain.candles import Candle


class BinanceSpotCandleClient:
    """Fetch closed Binance spot klines for technical/regime features."""

    BASE_URL = "https://api.binance.com"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_closed(
        self,
        symbol: str,
        *,
        interval: str,
        limit: int = 100,
    ) -> list[Candle]:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": str(limit + 1),
        }

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(
                f"{self.BASE_URL}/api/v3/klines",
                params=params,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(
                        f"Binance spot klines HTTP {response.status}: {payload}"
                    )

        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance kline response")

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        candles = [
            self._parse_row(symbol.upper(), interval, row)
            for row in payload
            if isinstance(row, list) and len(row) >= 7 and int(row[6]) < now_ms
        ]
        return candles[-limit:]

    @staticmethod
    def _parse_row(
        symbol: str,
        interval: str,
        row: list[Any],
    ) -> Candle:
        return Candle(
            symbol=symbol,
            interval=interval,
            open_time=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
            close_time=datetime.fromtimestamp(int(row[6]) / 1000, tz=UTC),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            closed=True,
        )
