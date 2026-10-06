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


def parse_order_book(
    payload: dict[str, object],
    *,
    symbol: str,
) -> OrderBookSnapshot:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Unexpected Bybit order-book payload")

    bids_raw = data.get("b")
    asks_raw = data.get("a")

    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise ValueError("Unexpected Bybit order-book levels")

    bids = tuple(
        OrderBookLevel(
            price=Decimal(str(price)),
            quantity=Decimal(str(quantity)),
        )
        for price, quantity in bids_raw
    )
    asks = tuple(
        OrderBookLevel(
            price=Decimal(str(price)),
            quantity=Decimal(str(quantity)),
        )
        for price, quantity in asks_raw
    )

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


class BybitSpotOrderBookCollector:
    """Streams Bybit public spot order-book snapshots/deltas.

    This first version emits the levels contained in each message. A stateful
    local-book merger can be added later when deeper replay fidelity is needed.
    """

    def __init__(
        self,
        symbols: list[str],
        *,
        depth: int = 50,
        reconnect_delay_seconds: float = 2.0,
    ) -> None:
        if not symbols:
            raise ValueError("At least one symbol is required")

        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._depth = depth
        self._reconnect_delay_seconds = reconnect_delay_seconds

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
        async with websockets.connect(
            BYBIT_SPOT_PUBLIC_URL,
            ping_interval=20,
            ping_timeout=20,
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

            async for message in websocket:
                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                payload = json.loads(raw)

                if not isinstance(payload, dict):
                    continue

                topic = str(payload.get("topic", ""))
                if not topic.startswith("orderbook."):
                    continue

                symbol = topic.rsplit(".", 1)[-1]

                yield parse_order_book(
                    payload,
                    symbol=symbol,
                )
