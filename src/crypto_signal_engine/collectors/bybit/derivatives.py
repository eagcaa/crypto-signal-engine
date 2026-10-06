import asyncio
import json
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


class BybitDerivativesClient:
    BASE_URL = "https://api.bybit.com"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_snapshot(self, symbol: str) -> ExchangeDerivativesSnapshot:
        symbol = symbol.upper()

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            ticker, oi_5m, oi_15m, ratio = await asyncio.gather(
                self._get(
                    session,
                    "/v5/market/tickers",
                    {"category": "linear", "symbol": symbol},
                ),
                self._get(
                    session,
                    "/v5/market/open-interest",
                    {
                        "category": "linear",
                        "symbol": symbol,
                        "intervalTime": "5min",
                        "limit": "2",
                    },
                ),
                self._get(
                    session,
                    "/v5/market/open-interest",
                    {
                        "category": "linear",
                        "symbol": symbol,
                        "intervalTime": "15min",
                        "limit": "2",
                    },
                ),
                self._get(
                    session,
                    "/v5/market/account-ratio",
                    {
                        "category": "linear",
                        "symbol": symbol,
                        "period": "5min",
                        "limit": "1",
                    },
                ),
            )

        ticker_row = self._first_result_row(ticker)
        oi_5m_rows = self._result_list(oi_5m)
        oi_15m_rows = self._result_list(oi_15m)
        ratio_row = self._first_result_row(ratio)

        open_interest = self._decimal_or_none(
            ticker_row.get("openInterest") if ticker_row else None
        )
        mark_price = self._decimal_or_none(
            ticker_row.get("markPrice") if ticker_row else None
        )

        return ExchangeDerivativesSnapshot(
            exchange=Exchange.BYBIT,
            symbol=symbol,
            timestamp=datetime.now(UTC),
            open_interest=open_interest,
            open_interest_value_usd=(
                open_interest * mark_price
                if open_interest is not None and mark_price is not None
                else None
            ),
            oi_change_5m_pct=self._history_change_pct(oi_5m_rows),
            oi_change_15m_pct=self._history_change_pct(oi_15m_rows),
            funding_rate=self._decimal_or_none(
                ticker_row.get("fundingRate") if ticker_row else None
            ),
            long_short_ratio=self._ratio_from_accounts(ratio_row),
            long_account_ratio=self._decimal_or_none(
                ratio_row.get("buyRatio") if ratio_row else None
            ),
            short_account_ratio=self._decimal_or_none(
                ratio_row.get("sellRatio") if ratio_row else None
            ),
            top_trader_long_short_ratio=None,
            taker_buy_sell_ratio=None,
            taker_buy_volume=None,
            taker_sell_volume=None,
        )

    async def _get(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        async with session.get(f"{self.BASE_URL}{path}", params=params) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400 or payload.get("retCode") != 0:
                raise RuntimeError(
                    f"Bybit derivatives error HTTP {response.status}: {payload}"
                )
            return payload

    @staticmethod
    def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        rows = result.get("list")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    @classmethod
    def _first_result_row(
        cls,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = cls._result_list(payload)
        return rows[0] if rows else None

    @classmethod
    def _history_change_pct(
        cls,
        rows: list[dict[str, Any]],
    ) -> Decimal | None:
        if len(rows) < 2:
            return None

        newer = cls._decimal_or_none(rows[0].get("openInterest"))
        older = cls._decimal_or_none(rows[1].get("openInterest"))

        if older in (None, Decimal("0")) or newer is None:
            return None

        return ((newer - older) / older) * Decimal("100")

    @classmethod
    def _ratio_from_accounts(
        cls,
        row: dict[str, Any] | None,
    ) -> Decimal | None:
        if row is None:
            return None

        buy = cls._decimal_or_none(row.get("buyRatio"))
        sell = cls._decimal_or_none(row.get("sellRatio"))

        if buy is None or sell in (None, Decimal("0")):
            return None
        return buy / sell

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))


class BybitLiquidationCollector:
    URL = "wss://stream.bybit.com/v5/public/linear"

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = [symbol.upper() for symbol in symbols]

    async def events(self):
        args = [f"allLiquidation.{symbol}" for symbol in self._symbols]

        while True:
            try:
                async with websockets.connect(self.URL, ping_interval=20) as websocket:
                    await websocket.send(
                        json.dumps({"op": "subscribe", "args": args})
                    )

                    async for message in websocket:
                        payload = json.loads(message)
                        for event in self.parse_message(payload):
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)

    @staticmethod
    def parse_message(payload: dict[str, Any]) -> list[LiquidationEvent]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []

        events: list[LiquidationEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            side = str(row.get("S", ""))
            if side == "Buy":
                position_side = LiquidatedPositionSide.LONG
            elif side == "Sell":
                position_side = LiquidatedPositionSide.SHORT
            else:
                continue

            try:
                events.append(
                    LiquidationEvent(
                        exchange=Exchange.BYBIT,
                        symbol=str(row["s"]).upper(),
                        event_time=datetime.fromtimestamp(
                            int(row["T"]) / 1000,
                            tz=UTC,
                        ),
                        position_side=position_side,
                        price=Decimal(str(row["p"])),
                        quantity=Decimal(str(row["v"])),
                    )
                )
            except (KeyError, ValueError):
                continue

        return events
