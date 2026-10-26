from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp


@dataclass(frozen=True, slots=True)
class TradeFlowSnapshot:
    market: str
    buy_usd: Decimal
    sell_usd: Decimal
    trade_count: int
    complete: bool = True

    @property
    def net_usd(self) -> Decimal:
        return self.buy_usd - self.sell_usd

    @property
    def buy_share_pct(self) -> float | None:
        total = self.buy_usd + self.sell_usd
        if total <= 0:
            return None
        return float(self.buy_usd / total * Decimal("100"))


class BinanceRecentFlowClient:
    """Recent taker buy/sell notional from public Binance aggregate trades."""

    SPOT_URL = "https://api.binance.com/api/v3/aggTrades"
    FUTURES_URL = "https://fapi.binance.com/fapi/v1/aggTrades"

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        request_limit: int = 1000,
        max_pages: int = 20,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._request_limit = min(max(int(request_limit), 1), 1000)
        self._max_pages = max(int(max_pages), 1)

    async def fetch_pair(
        self,
        symbol: str,
        *,
        minutes: int = 15,
        now: datetime | None = None,
    ) -> tuple[TradeFlowSnapshot | None, TradeFlowSnapshot | None]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        start = now - timedelta(minutes=minutes)
        spot, futures = await asyncio.gather(
            self._safe_fetch(self.SPOT_URL, "SPOT", symbol, start, now),
            self._safe_fetch(self.FUTURES_URL, "VADELİ", symbol, start, now),
        )
        return spot, futures

    async def _safe_fetch(
        self,
        url: str,
        market: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> TradeFlowSnapshot | None:
        try:
            return await self._fetch(url, market, symbol, start, end)
        except Exception as exc:  # noqa: BLE001
            print(
                "RECENT_FLOW unavailable "
                f"market={market} symbol={symbol}: {type(exc).__name__}: {exc}"
            )
            return None

    async def _fetch(
        self,
        url: str,
        market: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> TradeFlowSnapshot:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        buy = Decimal("0")
        sell = Decimal("0")
        count = 0
        from_id: int | None = None
        complete = True

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            for page in range(self._max_pages):
                params = {
                    "symbol": symbol.upper(),
                    "limit": str(self._request_limit),
                }
                if from_id is None:
                    params["startTime"] = str(start_ms)
                    params["endTime"] = str(end_ms)
                else:
                    params["fromId"] = str(from_id)

                async with session.get(url, params=params) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RuntimeError(
                            f"Binance aggTrades HTTP {response.status}: {payload}"
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
                    trade_ms = int(item["T"])
                    aggregate_id = int(item["a"])
                    last_id = aggregate_id
                    if trade_ms < start_ms:
                        continue
                    if trade_ms > end_ms:
                        reached_end = True
                        break

                    notional = Decimal(str(item["p"])) * Decimal(str(item["q"]))
                    # m=true means buyer is maker, therefore the taker sold.
                    if bool(item.get("m")):
                        sell += notional
                    else:
                        buy += notional
                    count += 1

                if reached_end or last_id is None or len(payload) < self._request_limit:
                    break
                from_id = last_id + 1
            else:
                complete = False

        return TradeFlowSnapshot(
            market=market,
            buy_usd=buy,
            sell_usd=sell,
            trade_count=count,
            complete=complete,
        )
