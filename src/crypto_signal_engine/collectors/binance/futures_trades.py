import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import websockets

from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide, TradeTick

logger = logging.getLogger(__name__)

BINANCE_FUTURES_STREAM_URL = "wss://fstream.binance.com/ws"


def parse_futures_agg_trade(payload: dict[str, object]) -> TradeTick:
    """Convert a Binance USD-M futures aggTrade event into TradeTick."""
    if payload.get("e") != "aggTrade":
        raise ValueError("Expected Binance futures aggTrade event")

    buyer_is_maker = bool(payload["m"])
    side = TradeSide.SELL if buyer_is_maker else TradeSide.BUY

    return TradeTick(
        exchange=Exchange.BINANCE,
        market_type=MarketType.FUTURES,
        symbol=str(payload["s"]),
        event_time=datetime.fromtimestamp(int(payload["E"]) / 1000, tz=UTC),
        price=Decimal(str(payload["p"])),
        quantity=Decimal(str(payload["q"])),
        side=side,
    )


class BinanceFuturesTradeCollector:
    """Streams Binance USD-M futures aggregate trades."""

    def __init__(
        self,
        symbols: list[str],
        *,
        base_url: str = BINANCE_FUTURES_STREAM_URL,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        if not symbols:
            raise ValueError("At least one symbol is required")

        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._base_url = base_url.rstrip("/")
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._receive_timeout_seconds = receive_timeout_seconds

    @property
    def stream_names(self) -> tuple[str, ...]:
        return tuple(f"{symbol.lower()}@aggTrade" for symbol in self._symbols)

    async def trades(self) -> AsyncIterator[TradeTick]:
        while True:
            try:
                async for trade in self._connection_trades():
                    yield trade
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Binance futures trade stream disconnected; reconnecting in %.1fs",
                    self._reconnect_delay_seconds,
                )
                await asyncio.sleep(self._reconnect_delay_seconds)

    async def _connection_trades(self) -> AsyncIterator[TradeTick]:
        url = self._build_url()
        logger.info("Connecting to Binance futures streams: %s", ", ".join(self.stream_names))

        async with websockets.connect(
            url,
            ping_interval=None,
            close_timeout=10,
            max_queue=4096,
        ) as websocket:
            logger.info("Binance futures trade stream connected: %s", url)
            first_trade = True

            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self._receive_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        "Binance futures trade stream received no messages for "
                        f"{self._receive_timeout_seconds:.0f}s"
                    ) from exc

                payload = self._decode_message(message)
                trade = parse_futures_agg_trade(payload)

                if first_trade:
                    logger.info(
                        "Binance futures first trade received: %s price=%s",
                        trade.symbol,
                        trade.price,
                    )
                    first_trade = False

                yield trade

    def _build_url(self) -> str:
        if len(self.stream_names) == 1:
            return f"{self._base_url}/{self.stream_names[0]}"

        streams = "/".join(self.stream_names)
        combined_base = self._base_url.removesuffix("/ws")
        return f"{combined_base}/stream?streams={streams}"

    @staticmethod
    def _decode_message(message: str | bytes) -> dict[str, object]:
        raw = message.decode("utf-8") if isinstance(message, bytes) else message
        payload = json.loads(raw)

        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]

        if not isinstance(payload, dict):
            raise ValueError("Unexpected Binance futures WebSocket payload")

        return payload
