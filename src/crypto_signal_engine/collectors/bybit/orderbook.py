import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import websockets

from crypto_signal_engine.collectors.bybit.trades import BYBIT_SPOT_PUBLIC_URL
from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    OrderBookLevel,
    OrderBookSnapshot,
)

logger = logging.getLogger(__name__)


class BybitLocalOrderBook:
    """Maintains a stateful local Bybit order book from snapshot + delta messages."""

    def __init__(self, *, depth: int) -> None:
        self._depth = depth
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._initialized = False

    def apply(
        self,
        payload: dict[str, object],
        *,
        symbol: str,
    ) -> OrderBookSnapshot | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Unexpected Bybit order-book payload")

        message_type = str(payload.get("type", ""))
        bids_raw = data.get("b", [])
        asks_raw = data.get("a", [])

        if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
            raise ValueError("Unexpected Bybit order-book levels")

        if message_type == "snapshot":
            self._bids.clear()
            self._asks.clear()
            self._apply_levels(self._bids, bids_raw)
            self._apply_levels(self._asks, asks_raw)
            self._initialized = True
        elif message_type == "delta":
            if not self._initialized:
                return None
            self._apply_levels(self._bids, bids_raw)
            self._apply_levels(self._asks, asks_raw)
        else:
            return None

        bids = tuple(
            OrderBookLevel(price=price, quantity=quantity)
            for price, quantity in sorted(
                self._bids.items(),
                key=lambda item: item[0],
                reverse=True,
            )[: self._depth]
        )
        asks = tuple(
            OrderBookLevel(price=price, quantity=quantity)
            for price, quantity in sorted(
                self._asks.items(),
                key=lambda item: item[0],
            )[: self._depth]
        )

        if not bids or not asks:
            return None

        ts = int(payload.get("ts") or 0)
        event_time = (
            datetime.fromtimestamp(ts / 1000, tz=UTC)
            if ts
            else datetime.now(UTC)
        )

        return OrderBookSnapshot(
            exchange=Exchange.BYBIT,
            market_type=MarketType.SPOT,
            symbol=symbol.upper(),
            event_time=event_time,
            bids=bids,
            asks=asks,
        )

    @staticmethod
    def _apply_levels(
        target: dict[Decimal, Decimal],
        levels: list[object],
    ) -> None:
        for raw_level in levels:
            if not isinstance(raw_level, list) or len(raw_level) < 2:
                continue

            price = Decimal(str(raw_level[0]))
            quantity = Decimal(str(raw_level[1]))

            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity


class BybitSpotOrderBookCollector:
    """Streams and maintains Bybit public spot order books."""

    def __init__(
        self,
        symbols: list[str],
        *,
        depth: int = 50,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        if not symbols:
            raise ValueError("At least one symbol is required")

        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._depth = depth
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._receive_timeout_seconds = receive_timeout_seconds

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(f"orderbook.{self._depth}.{symbol}" for symbol in self._symbols)

    async def snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        while True:
            try:
                async for snapshot in self._connection_snapshots():
                    yield snapshot
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Bybit spot order-book stream disconnected; reconnecting in %.1fs",
                    self._reconnect_delay_seconds,
                )
                await asyncio.sleep(self._reconnect_delay_seconds)

    async def _connection_snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        books = {
            symbol: BybitLocalOrderBook(depth=self._depth)
            for symbol in self._symbols
        }

        async with websockets.connect(
            BYBIT_SPOT_PUBLIC_URL,
            ping_interval=None,
            close_timeout=10,
            max_queue=4096,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": list(self.topics),
                    }
                )
            )

            logger.info(
                "Bybit spot order-book stream connected: %s",
                BYBIT_SPOT_PUBLIC_URL,
            )

            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self._receive_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        "Bybit spot order-book stream received no messages for "
                        f"{self._receive_timeout_seconds:.0f}s"
                    ) from exc

                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                payload = json.loads(raw)

                if not isinstance(payload, dict):
                    continue

                topic = str(payload.get("topic", ""))
                if not topic.startswith("orderbook."):
                    continue

                symbol = topic.rsplit(".", 1)[-1].upper()
                book = books.get(symbol)

                if book is None:
                    continue

                snapshot = book.apply(
                    payload,
                    symbol=symbol,
                )

                if snapshot is not None:
                    yield snapshot
