import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import websockets

from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide, TradeTick

logger = logging.getLogger(__name__)

BYBIT_SPOT_PUBLIC_URL = "wss://stream.bybit.com/v5/public/spot"
BYBIT_LINEAR_PUBLIC_URL = "wss://stream.bybit.com/v5/public/linear"


def parse_public_trade(
    payload: dict[str, object],
    *,
    market_type: MarketType,
) -> tuple[TradeTick, ...]:
    """Convert a Bybit publicTrade message into normalized TradeTick items."""
    topic = str(payload.get("topic", ""))
    if not topic.startswith("publicTrade."):
        raise ValueError("Expected Bybit publicTrade event")

    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Unexpected Bybit publicTrade payload")

    trades: list[TradeTick] = []

    for item in data:
        if not isinstance(item, dict):
            continue

        side_raw = str(item["S"]).lower()
        if side_raw == "buy":
            side = TradeSide.BUY
        elif side_raw == "sell":
            side = TradeSide.SELL
        else:
            raise ValueError(f"Unexpected Bybit trade side: {side_raw}")

        event_time_ms = int(item.get("T") or payload.get("ts") or 0)

        trades.append(
            TradeTick(
                exchange=Exchange.BYBIT,
                market_type=market_type,
                symbol=str(item["s"]),
                event_time=datetime.fromtimestamp(event_time_ms / 1000, tz=UTC),
                price=Decimal(str(item["p"])),
                quantity=Decimal(str(item["v"])),
                side=side,
            )
        )

    return tuple(trades)


class _BybitTradeCollector:
    def __init__(
        self,
        symbols: list[str],
        *,
        market_type: MarketType,
        base_url: str,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        if not symbols:
            raise ValueError("At least one symbol is required")

        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._market_type = market_type
        self._base_url = base_url
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._receive_timeout_seconds = receive_timeout_seconds

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(f"publicTrade.{symbol}" for symbol in self._symbols)

    async def trades(self) -> AsyncIterator[TradeTick]:
        while True:
            try:
                async for trade in self._connection_trades():
                    yield trade
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Bybit %s trade stream disconnected; reconnecting in %.1fs",
                    self._market_type.value,
                    self._reconnect_delay_seconds,
                )
                await asyncio.sleep(self._reconnect_delay_seconds)

    async def _connection_trades(self) -> AsyncIterator[TradeTick]:
        logger.info(
            "Connecting to Bybit %s streams: %s",
            self._market_type.value,
            ", ".join(self.topics),
        )

        async with websockets.connect(
            self._base_url,
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
                "Bybit %s trade stream connected: %s",
                self._market_type.value,
                self._base_url,
            )
            first_trade = True

            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self._receive_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"Bybit {self._market_type.value} trade stream "
                        f"received no messages for "
                        f"{self._receive_timeout_seconds:.0f}s"
                    ) from exc

                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                payload = json.loads(raw)

                if not isinstance(payload, dict):
                    continue

                if "topic" not in payload:
                    continue

                for trade in parse_public_trade(
                    payload,
                    market_type=self._market_type,
                ):
                    if first_trade:
                        logger.info(
                            "Bybit %s first trade received: %s price=%s",
                            self._market_type.value,
                            trade.symbol,
                            trade.price,
                        )
                        first_trade = False
                    yield trade


class BybitSpotTradeCollector(_BybitTradeCollector):
    def __init__(
        self,
        symbols: list[str],
        *,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        super().__init__(
            symbols,
            market_type=MarketType.SPOT,
            base_url=BYBIT_SPOT_PUBLIC_URL,
            reconnect_delay_seconds=reconnect_delay_seconds,
            receive_timeout_seconds=receive_timeout_seconds,
        )


class BybitFuturesTradeCollector(_BybitTradeCollector):
    def __init__(
        self,
        symbols: list[str],
        *,
        reconnect_delay_seconds: float = 2.0,
        receive_timeout_seconds: float = 45.0,
    ) -> None:
        super().__init__(
            symbols,
            market_type=MarketType.FUTURES,
            base_url=BYBIT_LINEAR_PUBLIC_URL,
            reconnect_delay_seconds=reconnect_delay_seconds,
            receive_timeout_seconds=receive_timeout_seconds,
        )
