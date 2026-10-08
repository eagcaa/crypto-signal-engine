import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    TradeSide,
    TradeTick,
)
from crypto_signal_engine.features.orderbook import OrderBookMetrics
from crypto_signal_engine.market import MarketSnapshotAggregator


def make_trade(
    *,
    exchange: Exchange,
    market_type: MarketType,
    side: TradeSide,
    quantity: str,
    price: str = "100",
) -> TradeTick:
    return TradeTick(
        exchange=exchange,
        market_type=market_type,
        symbol="BTCUSDT",
        event_time=datetime.now(UTC),
        price=Decimal(price),
        quantity=Decimal(quantity),
        side=side,
    )


def test_market_snapshot_aggregates_cvd_and_order_book_state() -> None:
    async def run() -> None:
        aggregator = MarketSnapshotAggregator("BTCUSDT")

        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                side=TradeSide.BUY,
                quantity="5",
                price="101",
            )
        )
        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BYBIT,
                market_type=MarketType.SPOT,
                side=TradeSide.SELL,
                quantity="1",
            )
        )
        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BINANCE,
                market_type=MarketType.FUTURES,
                side=TradeSide.SELL,
                quantity="2",
            )
        )
        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BYBIT,
                market_type=MarketType.FUTURES,
                side=TradeSide.BUY,
                quantity="1",
            )
        )

        await aggregator.update_order_book(
            Exchange.BINANCE,
            OrderBookMetrics(
                bid_volume=Decimal("10"),
                ask_volume=Decimal("5"),
                imbalance=Decimal("0.4"),
                best_bid=Decimal("100"),
                best_ask=Decimal("101"),
                spread=Decimal("1"),
            ),
        )
        await aggregator.update_order_book(
            Exchange.BYBIT,
            OrderBookMetrics(
                bid_volume=Decimal("8"),
                ask_volume=Decimal("9"),
                imbalance=Decimal("-0.2"),
                best_bid=Decimal("100"),
                best_ask=Decimal("101"),
                spread=Decimal("1"),
            ),
        )

        snapshot = await aggregator.snapshot()

        assert snapshot.price == Decimal("101")
        assert snapshot.binance_spot_cvd == Decimal("5")
        assert snapshot.bybit_spot_cvd == Decimal("-1")
        assert snapshot.binance_futures_cvd == Decimal("-2")
        assert snapshot.bybit_futures_cvd == Decimal("1")
        assert snapshot.spot_cvd_total == Decimal("4")
        assert snapshot.futures_cvd_total == Decimal("-1")
        assert snapshot.spot_futures_divergence == Decimal("5")
        assert snapshot.binance_book_imbalance == Decimal("0.4")
        assert snapshot.bybit_book_imbalance == Decimal("-0.2")
        assert snapshot.cross_exchange_book_divergence == Decimal("0.6")
        assert snapshot.buy_pressure == Decimal("0.1")
        assert snapshot.sell_pressure == Decimal("0")

    asyncio.run(run())


def test_market_snapshot_keeps_binance_spot_as_canonical_price() -> None:
    async def run() -> None:
        aggregator = MarketSnapshotAggregator("BTCUSDT")

        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                side=TradeSide.BUY,
                quantity="1",
                price="101",
            )
        )
        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BYBIT,
                market_type=MarketType.SPOT,
                side=TradeSide.BUY,
                quantity="1",
                price="102",
            )
        )
        await aggregator.update_trade(
            make_trade(
                exchange=Exchange.BYBIT,
                market_type=MarketType.FUTURES,
                side=TradeSide.BUY,
                quantity="1",
                price="103",
            )
        )

        snapshot = await aggregator.snapshot()

        assert snapshot.price == Decimal("101")

    asyncio.run(run())
