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

    binance_spot_cvd: Decimal | None
    binance_futures_cvd: Decimal | None
    bybit_spot_cvd: Decimal | None
    bybit_futures_cvd: Decimal | None

    binance_book_imbalance: Decimal | None
    bybit_book_imbalance: Decimal | None

    spot_cvd_total: Decimal | None
    futures_cvd_total: Decimal | None
    spot_futures_divergence: Decimal | None

    cross_exchange_book_divergence: Decimal | None
    buy_pressure: Decimal | None
    sell_pressure: Decimal | None

    binance_spot_age_ms: int | None
    binance_futures_age_ms: int | None
    bybit_spot_age_ms: int | None
    bybit_futures_age_ms: int | None
    binance_book_age_ms: int | None
    bybit_book_age_ms: int | None

    data_quality: Decimal


class MarketSnapshotAggregator:
    """Maintains the latest in-memory market state for one symbol."""

    def __init__(
        self,
        symbol: str,
        *,
        stale_after_ms: int = 10_000,
    ) -> None:
        self.symbol = symbol.upper()
        self._stale_after_ms = stale_after_ms

        self._cvd: dict[tuple[Exchange, MarketType], CvdAccumulator] = {
            (Exchange.BINANCE, MarketType.SPOT): CvdAccumulator(),
            (Exchange.BINANCE, MarketType.FUTURES): CvdAccumulator(),
            (Exchange.BYBIT, MarketType.SPOT): CvdAccumulator(),
            (Exchange.BYBIT, MarketType.FUTURES): CvdAccumulator(),
        }

        self._trade_last_seen: dict[tuple[Exchange, MarketType], datetime] = {}
        self._book_last_seen: dict[Exchange, datetime] = {}

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
            self._trade_last_seen[key] = trade.event_time
            self._latest_price = trade.price

    async def update_order_book(
        self,
        exchange: Exchange,
        metrics: OrderBookMetrics,
        *,
        event_time: datetime | None = None,
    ) -> None:
        async with self._lock:
            self._books[exchange] = metrics
            self._book_last_seen[exchange] = event_time or datetime.now(UTC)

    async def snapshot(self, *, timestamp: datetime | None = None) -> MarketSnapshot:
        async with self._lock:
            now = timestamp or datetime.now(UTC)

            binance_spot_cvd = self._cvd_value(
                Exchange.BINANCE,
                MarketType.SPOT,
            )
            binance_futures_cvd = self._cvd_value(
                Exchange.BINANCE,
                MarketType.FUTURES,
            )
            bybit_spot_cvd = self._cvd_value(
                Exchange.BYBIT,
                MarketType.SPOT,
            )
            bybit_futures_cvd = self._cvd_value(
                Exchange.BYBIT,
                MarketType.FUTURES,
            )

            spot_cvd_total = self._sum_if_complete(
                binance_spot_cvd,
                bybit_spot_cvd,
            )
            futures_cvd_total = self._sum_if_complete(
                binance_futures_cvd,
                bybit_futures_cvd,
            )

            spot_futures_divergence = None
            if spot_cvd_total is not None and futures_cvd_total is not None:
                spot_futures_divergence = (
                    spot_cvd_total - futures_cvd_total
                )

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

            binance_spot_age_ms = self._trade_age_ms(
                now,
                Exchange.BINANCE,
                MarketType.SPOT,
            )
            binance_futures_age_ms = self._trade_age_ms(
                now,
                Exchange.BINANCE,
                MarketType.FUTURES,
            )
            bybit_spot_age_ms = self._trade_age_ms(
                now,
                Exchange.BYBIT,
                MarketType.SPOT,
            )
            bybit_futures_age_ms = self._trade_age_ms(
                now,
                Exchange.BYBIT,
                MarketType.FUTURES,
            )
            binance_book_age_ms = self._book_age_ms(
                now,
                Exchange.BINANCE,
            )
            bybit_book_age_ms = self._book_age_ms(
                now,
                Exchange.BYBIT,
            )

            ages = (
                binance_spot_age_ms,
                binance_futures_age_ms,
                bybit_spot_age_ms,
                bybit_futures_age_ms,
                binance_book_age_ms,
                bybit_book_age_ms,
            )
            fresh_count = sum(
                1
                for age in ages
                if age is not None and age <= self._stale_after_ms
            )
            data_quality = Decimal(fresh_count) / Decimal(len(ages))

            return MarketSnapshot(
                symbol=self.symbol,
                timestamp=now,
                price=self._latest_price,
                binance_spot_cvd=binance_spot_cvd,
                binance_futures_cvd=binance_futures_cvd,
                bybit_spot_cvd=bybit_spot_cvd,
                bybit_futures_cvd=bybit_futures_cvd,
                binance_book_imbalance=binance_imbalance,
                bybit_book_imbalance=bybit_imbalance,
                spot_cvd_total=spot_cvd_total,
                futures_cvd_total=futures_cvd_total,
                spot_futures_divergence=spot_futures_divergence,
                cross_exchange_book_divergence=cross_exchange_book_divergence,
                buy_pressure=buy_pressure,
                sell_pressure=sell_pressure,
                binance_spot_age_ms=binance_spot_age_ms,
                binance_futures_age_ms=binance_futures_age_ms,
                bybit_spot_age_ms=bybit_spot_age_ms,
                bybit_futures_age_ms=bybit_futures_age_ms,
                binance_book_age_ms=binance_book_age_ms,
                bybit_book_age_ms=bybit_book_age_ms,
                data_quality=data_quality,
            )

    def _cvd_value(
        self,
        exchange: Exchange,
        market_type: MarketType,
    ) -> Decimal | None:
        key = (exchange, market_type)
        if key not in self._trade_last_seen:
            return None
        return self._cvd[key].cvd

    def _trade_age_ms(
        self,
        now: datetime,
        exchange: Exchange,
        market_type: MarketType,
    ) -> int | None:
        last_seen = self._trade_last_seen.get((exchange, market_type))
        return self._age_ms(now, last_seen)

    def _book_age_ms(
        self,
        now: datetime,
        exchange: Exchange,
    ) -> int | None:
        last_seen = self._book_last_seen.get(exchange)
        return self._age_ms(now, last_seen)

    @staticmethod
    def _age_ms(
        now: datetime,
        last_seen: datetime | None,
    ) -> int | None:
        if last_seen is None:
            return None
        return max(0, int((now - last_seen).total_seconds() * 1000))

    @staticmethod
    def _sum_if_complete(
        left: Decimal | None,
        right: Decimal | None,
    ) -> Decimal | None:
        if left is None or right is None:
            return None
        return left + right
