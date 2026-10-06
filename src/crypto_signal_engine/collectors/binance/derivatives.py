import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
    LiquidatedPositionSide,
    LiquidationEvent,
)
from crypto_signal_engine.domain.models import Exchange


class BinanceDerivativesClient:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_snapshot(self, symbol: str) -> ExchangeDerivativesSnapshot:
        symbol = symbol.upper()

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            (
                oi,
                oi_5m,
                oi_15m,
                premium,
                global_ratio,
                top_ratio,
                taker_ratio,
            ) = await asyncio.gather(
                self._get(session, "/fapi/v1/openInterest", {"symbol": symbol}),
                self._get(
                    session,
                    "/futures/data/openInterestHist",
                    {"symbol": symbol, "period": "5m", "limit": "2"},
                ),
                self._get(
                    session,
                    "/futures/data/openInterestHist",
                    {"symbol": symbol, "period": "15m", "limit": "2"},
                ),
                self._get(session, "/fapi/v1/premiumIndex", {"symbol": symbol}),
                self._get(
                    session,
                    "/futures/data/globalLongShortAccountRatio",
                    {"symbol": symbol, "period": "5m", "limit": "1"},
                ),
                self._get(
                    session,
                    "/futures/data/topLongShortAccountRatio",
                    {"symbol": symbol, "period": "5m", "limit": "1"},
                ),
                self._get(
                    session,
                    "/futures/data/takerlongshortRatio",
                    {"symbol": symbol, "period": "5m", "limit": "1"},
                ),
            )

        global_row = self._last_dict(global_ratio)
        top_row = self._last_dict(top_ratio)
        taker_row = self._last_dict(taker_ratio)
        latest_oi_row = self._last_dict(oi_5m)

        return ExchangeDerivativesSnapshot(
            exchange=Exchange.BINANCE,
            symbol=symbol,
            timestamp=datetime.now(UTC),
            open_interest=self._decimal_or_none(oi.get("openInterest")),
            open_interest_value_usd=self._decimal_or_none(
                latest_oi_row.get("sumOpenInterestValue")
                if latest_oi_row
                else None
            ),
            oi_change_5m_pct=self._history_change_pct(
                oi_5m,
                "sumOpenInterestValue",
            ),
            oi_change_15m_pct=self._history_change_pct(
                oi_15m,
                "sumOpenInterestValue",
            ),
            funding_rate=self._decimal_or_none(premium.get("lastFundingRate")),
            long_short_ratio=self._decimal_or_none(
                global_row.get("longShortRatio") if global_row else None
            ),
            long_account_ratio=self._decimal_or_none(
                global_row.get("longAccount") if global_row else None
            ),
            short_account_ratio=self._decimal_or_none(
                global_row.get("shortAccount") if global_row else None
            ),
            top_trader_long_short_ratio=self._decimal_or_none(
                top_row.get("longShortRatio") if top_row else None
            ),
            taker_buy_sell_ratio=self._decimal_or_none(
                taker_row.get("buySellRatio") if taker_row else None
            ),
            taker_buy_volume=self._decimal_or_none(
                taker_row.get("buyVol") if taker_row else None
            ),
            taker_sell_volume=self._decimal_or_none(
                taker_row.get("sellVol") if taker_row else None
            ),
        )

    async def _get(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: dict[str, str],
    ) -> Any:
        async with session.get(f"{self.BASE_URL}{path}", params=params) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(
                    f"Binance derivatives HTTP {response.status}: {payload}"
                )
            return payload

    @staticmethod
    def _last_dict(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, list) or not value:
            return None
        row = value[-1]
        return row if isinstance(row, dict) else None

    @classmethod
    def _history_change_pct(
        cls,
        rows: Any,
        field: str,
    ) -> Decimal | None:
        if not isinstance(rows, list) or len(rows) < 2:
            return None

        older = cls._decimal_or_none(rows[-2].get(field))
        newer = cls._decimal_or_none(rows[-1].get(field))

        if older in (None, Decimal("0")) or newer is None:
            return None

        return ((newer - older) / older) * Decimal("100")

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))


class BinanceLiquidationCollector:
    BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = [symbol.lower() for symbol in symbols]

    async def events(self):
        streams = "/".join(f"{symbol}@forceOrder" for symbol in self._symbols)
        url = (
            f"{self.BASE_URL}/{streams}"
            if len(self._symbols) == 1
            else f"wss://fstream.binance.com/stream?streams={streams}"
        )

        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as websocket:
                    async for message in websocket:
                        import json

                        payload = json.loads(message)
                        data = payload.get("data", payload)
                        event = self.parse_message(data)
                        if event is not None:
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)

    @staticmethod
    def parse_message(payload: dict[str, Any]) -> LiquidationEvent | None:
        order = payload.get("o")
        if not isinstance(order, dict):
            return None

        side = str(order.get("S", "")).upper()
        if side == "SELL":
            position_side = LiquidatedPositionSide.LONG
        elif side == "BUY":
            position_side = LiquidatedPositionSide.SHORT
        else:
            return None

        price = order.get("ap") or order.get("p")
        quantity = order.get("z") or order.get("q")
        event_ms = payload.get("E") or order.get("T")

        if not price or not quantity or not event_ms:
            return None

        return LiquidationEvent(
            exchange=Exchange.BINANCE,
            symbol=str(order.get("s", "")).upper(),
            event_time=datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC),
            position_side=position_side,
            price=Decimal(str(price)),
            quantity=Decimal(str(quantity)),
        )
