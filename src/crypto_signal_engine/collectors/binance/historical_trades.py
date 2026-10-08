from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp

from crypto_signal_engine.replay.models import ReplayPricePoint


class BinanceSpotHistoricalTradeClient:
    """Fetch Binance spot aggregate trades for exact first-touch replay."""

    BASE_URL = "https://api.binance.com"
    MAX_TIME_WINDOW = timedelta(minutes=59, seconds=59)

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        request_limit: int = 1000,
    ) -> None:
        if request_limit <= 0 or request_limit > 1000:
            raise ValueError("request_limit must be in [1, 1000]")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._request_limit = request_limit

    async def fetch_price_points(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[ReplayPricePoint]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if end <= start:
            raise ValueError("end must be after start")

        symbol = symbol.upper()
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        points: list[ReplayPricePoint] = []

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            window_start = start
            while window_start < end:
                window_end = min(
                    window_start + self.MAX_TIME_WINDOW,
                    end,
                )
                points.extend(
                    await self._fetch_window(
                        session,
                        symbol=symbol,
                        start=window_start,
                        end=window_end,
                    )
                )
                window_start = window_end + timedelta(milliseconds=1)

        points.sort(key=lambda point: point.timestamp)
        return points

    async def fetch_price_points_for_windows(
        self,
        symbol: str,
        *,
        windows: list[tuple[datetime, datetime]],
    ) -> list[ReplayPricePoint]:
        if not windows:
            return []

        normalized: list[tuple[datetime, datetime]] = []
        for start, end in windows:
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("window start/end must be timezone-aware")
            if end <= start:
                continue
            normalized.append(
                (
                    start.astimezone(UTC),
                    end.astimezone(UTC),
                )
            )

        if not normalized:
            return []

        normalized.sort(key=lambda item: item[0])
        merged: list[list[datetime]] = []

        for start, end in normalized:
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)

        symbol = symbol.upper()
        points: list[ReplayPricePoint] = []

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            for merged_start, merged_end in merged:
                window_start = merged_start
                while window_start < merged_end:
                    window_end = min(
                        window_start + self.MAX_TIME_WINDOW,
                        merged_end,
                    )
                    points.extend(
                        await self._fetch_window(
                            session,
                            symbol=symbol,
                            start=window_start,
                            end=window_end,
                        )
                    )
                    window_start = window_end + timedelta(milliseconds=1)

        points.sort(key=lambda point: point.timestamp)
        return points

    async def _fetch_window(
        self,
        session: aiohttp.ClientSession,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[ReplayPricePoint]:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        points: list[ReplayPricePoint] = []
        from_id: int | None = None

        while True:
            params = {
                "symbol": symbol,
                "limit": str(self._request_limit),
            }
            if from_id is None:
                params["startTime"] = str(start_ms)
                params["endTime"] = str(end_ms)
            else:
                params["fromId"] = str(from_id)

            async with session.get(
                f"{self.BASE_URL}/api/v3/aggTrades",
                params=params,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(
                        "Binance spot aggTrades HTTP "
                        f"{response.status}: {payload}"
                    )

            if not isinstance(payload, list):
                raise RuntimeError("Unexpected Binance aggTrades response")
            if not payload:
                break

            last_id: int | None = None
            reached_end = False

            for item in payload:
                if not isinstance(item, dict):
                    continue

                event_ms = int(item["T"])
                aggregate_id = int(item["a"])
                last_id = aggregate_id

                if event_ms < start_ms:
                    continue
                if event_ms > end_ms:
                    reached_end = True
                    break

                points.append(
                    ReplayPricePoint(
                        symbol=symbol,
                        timestamp=datetime.fromtimestamp(
                            event_ms / 1000,
                            tz=UTC,
                        ),
                        price=Decimal(str(item["p"])),
                    )
                )

            if reached_end or last_id is None:
                break
            if len(payload) < self._request_limit:
                break

            from_id = last_id + 1

        return points
