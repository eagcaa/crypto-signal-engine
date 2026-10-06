import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.domain.models import Exchange, MarketType, TradeTick
from crypto_signal_engine.features.cvd import CvdAccumulator
from crypto_signal_engine.features.orderbook import OrderBookMetrics


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    price: Decimal | None

    binance_spot_cvd: Decimal
    binance_futures_cvd: Decimal
    bybit_spot_cvd: Decimal
    bybit_futures_cvd: Decimal

    binance_book_imbalance: Decimal | None
    bybit_book_imbalance: Decimal | None

    spot_cvd_total: Decimal
    futures_cvd_total: Decimal
    spot_futures_divergence: Decimal

    cross_exchange_book_divergence: Decimal | None
    buy_pressure: Decimal | None
    sell_pressure: Decimal | None


class MarketSnapshotAggregator:
    """Maintains the latest in-memory market state for one symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()

        self._cvd: dict[tuple[Exchange, MarketType], CvdAccumulator] = {
            (Exchange.BINANCE, MarketType.SPOT): CvdAccumulator(),
            (Exchange.BINANCE, MarketType.FUTURES): CvdAccumulator(),
            (Exchange.BYBIT, MarketType.SPOT): CvdAccumulator(),
            (Exchange.BYBIT, MarketType.FUTURES): CvdAccumulator(),
        }

        self._latest_price: Decimal | None = None
        self._books: dict[Exchange, OrderBookMetrics] = {}
        self._lock = asyncio.Lock()

    async def update_trade(self, trade: TradeTick) -> None:
        if trade.symbol.upper() != self.symbol:
            return

        key = (trade.exchange, trade.market_type)
        accumulator = self._cvd.get(key)
        if accumulator is None:
            return

        async with self._lock:
            accumulator.update(trade)
            self._latest_price = trade.price

    async def update_order_book(
        self,
        exchange: Exchange,
        metrics: OrderBookMetrics,
    ) -> None:
        async with self._lock:
            self._books[exchange] = metrics

    async def snapshot(self, *, timestamp: datetime | None = None) -> MarketSnapshot:
        async with self._lock:
            binance_spot_cvd = self._cvd[
                (Exchange.BINANCE, MarketType.SPOT)
            ].cvd
            binance_futures_cvd = self._cvd[
                (Exchange.BINANCE, MarketType.FUTURES)
            ].cvd
            bybit_spot_cvd = self._cvd[
                (Exchange.BYBIT, MarketType.SPOT)
            ].cvd
            bybit_futures_cvd = self._cvd[
                (Exchange.BYBIT, MarketType.FUTURES)
            ].cvd

            spot_cvd_total = binance_spot_cvd + bybit_spot_cvd
            futures_cvd_total = binance_futures_cvd + bybit_futures_cvd

            binance_book = self._books.get(Exchange.BINANCE)
            bybit_book = self._books.get(Exchange.BYBIT)

            binance_imbalance = (
                binance_book.imbalance if binance_book is not None else None
            )
            bybit_imbalance = (
                bybit_book.imbalance if bybit_book is not None else None
            )

            if binance_imbalance is not None and bybit_imbalance is not None:
                cross_exchange_book_divergence = (
                    binance_imbalance - bybit_imbalance
                )
                combined_book_imbalance = (
                    binance_imbalance + bybit_imbalance
                ) / Decimal("2")
            elif binance_imbalance is not None:
                cross_exchange_book_divergence = None
                combined_book_imbalance = binance_imbalance
            elif bybit_imbalance is not None:
                cross_exchange_book_divergence = None
                combined_book_imbalance = bybit_imbalance
            else:
                cross_exchange_book_divergence = None
                combined_book_imbalance = None

            buy_pressure = None
            sell_pressure = None

            if combined_book_imbalance is not None:
                buy_pressure = max(combined_book_imbalance, Decimal("0"))
                sell_pressure = max(-combined_book_imbalance, Decimal("0"))

            return MarketSnapshot(
                symbol=self.symbol,
                timestamp=timestamp or datetime.now(UTC),
                price=self._latest_price,
                binance_spot_cvd=binance_spot_cvd,
                binance_futures_cvd=binance_futures_cvd,
                bybit_spot_cvd=bybit_spot_cvd,
                bybit_futures_cvd=bybit_futures_cvd,
                binance_book_imbalance=binance_imbalance,
                bybit_book_imbalance=bybit_imbalance,
                spot_cvd_total=spot_cvd_total,
                futures_cvd_total=futures_cvd_total,
                spot_futures_divergence=spot_cvd_total - futures_cvd_total,
                cross_exchange_book_divergence=cross_exchange_book_divergence,
                buy_pressure=buy_pressure,
                sell_pressure=sell_pressure,
            )
