import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import websockets

from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    OrderBookLevel,
    OrderBookSnapshot,
)

logger = logging.getLogger(__name__)

BINANCE_SPOT_STREAM_BASE = "wss://stream.binance.com:9443/ws"


def parse_depth_snapshot(
    payload: dict[str, object],
    *,
    symbol: str,
    event_time: datetime,
) -> OrderBookSnapshot:
    bids_raw = payload.get("bids")
    asks_raw = payload.get("asks")

    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise ValueError("Unexpected Binance depth payload")

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

    return OrderBookSnapshot(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol=symbol.upper(),
        event_time=event_time,
        bids=bids,
        asks=asks,
    )


class BinanceSpotOrderBookCollector:
    """Streams Binance top-N partial order-book snapshots."""

    def __init__(
        self,
        symbols: list[str],
        *,
        depth: int = 20,
        update_ms: int = 100,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        if not symbols:
            raise ValueError("At least one symbol is required")
        if depth not in {5, 10, 20}:
            raise ValueError("Binance partial depth supports 5, 10 or 20 levels")
        if update_ms not in {100, 1000}:
            raise ValueError("update_ms must be 100 or 1000")

        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._depth = depth
        self._update_ms = update_ms
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._receive_timeout_seconds = receive_timeout_seconds

    @property
    def stream_names(self) -> tuple[str, ...]:
        suffix = f"@depth{self._depth}@{self._update_ms}ms"
        return tuple(f"{symbol.lower()}{suffix}" for symbol in self._symbols)

    async def snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        while True:
            try:
                async for snapshot in self._connection_snapshots():
                    yield snapshot
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Binance order-book stream disconnected; reconnecting in %.1fs",
                    self._reconnect_delay_seconds,
                )
                await asyncio.sleep(self._reconnect_delay_seconds)

    async def _connection_snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        url = self._build_url()

        async with websockets.connect(
            url,
            ping_interval=None,
            close_timeout=10,
            max_queue=4096,
        ) as websocket:
            logger.info("Binance order-book stream connected: %s", url)
            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self._receive_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        "Binance order-book stream received no messages for "
                        f"{self._receive_timeout_seconds:.0f}s"
                    ) from exc
                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                payload = json.loads(raw)

                stream_name = None
                if isinstance(payload, dict) and "stream" in payload and "data" in payload:
                    stream_name = str(payload["stream"])
                    payload = payload["data"]

                if not isinstance(payload, dict):
                    continue

                if stream_name:
                    symbol = stream_name.split("@", 1)[0].upper()
                elif len(self._symbols) == 1:
                    symbol = self._symbols[0]
                else:
                    continue

                event_ms = int(payload.get("E") or 0)
                event_time = (
                    datetime.fromtimestamp(event_ms / 1000, tz=UTC)
                    if event_ms
                    else datetime.now(UTC)
                )

                yield parse_depth_snapshot(
                    payload,
                    symbol=symbol,
                    event_time=event_time,
                )

    def _build_url(self) -> str:
        if len(self.stream_names) == 1:
            return f"{BINANCE_SPOT_STREAM_BASE}/{self.stream_names[0]}"

        streams = "/".join(self.stream_names)
        return f"wss://stream.binance.com:9443/stream?streams={streams}"
