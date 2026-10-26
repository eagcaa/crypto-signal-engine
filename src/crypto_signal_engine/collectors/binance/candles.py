from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp

from crypto_signal_engine.domain.candles import Candle


class BinanceSpotCandleClient:
    """Fetch closed Binance spot klines for technical/regime features."""

    BASE_URL = "https://api.binance.com"
    MAX_KLINE_LIMIT = 1000

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    @classmethod
    def _api_limit(cls, requested_limit: int) -> int:
        return min(max(1, int(requested_limit)) + 1, cls.MAX_KLINE_LIMIT)

    async def fetch_closed(
        self,
        symbol: str,
        *,
        interval: str,
        limit: int = 100,
        start_time: datetime | None = None,
    ) -> list[Candle]:
        requested_limit = max(1, int(limit))
        api_limit = self._api_limit(requested_limit)
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": str(api_limit),
        }
        if start_time is not None:
            params["startTime"] = str(int(start_time.timestamp() * 1000))

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
        return candles[-requested_limit:]

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
            quote_volume=Decimal(str(row[7])) if len(row) > 7 else Decimal("0"),
            trade_count=int(row[8]) if len(row) > 8 else 0,
            taker_buy_base_volume=(
                Decimal(str(row[9])) if len(row) > 9 else Decimal("0")
            ),
            taker_buy_quote_volume=(
                Decimal(str(row[10])) if len(row) > 10 else Decimal("0")
            ),
            closed=True,
        )
