from decimal import Decimal

from crypto_signal_engine.collectors.bybit.orderbook import (
    BybitSpotOrderBookCollector,
    parse_order_book,
)


def test_parse_bybit_order_book() -> None:
    snapshot = parse_order_book(
        {
            "topic": "orderbook.50.BTCUSDT",
            "ts": 1720000000123,
            "data": {
                "b": [["100", "2"], ["99", "3"]],
                "a": [["101", "4"], ["102", "5"]],
            },
        },
        symbol="BTCUSDT",
    )

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.bids[0].quantity == Decimal("2")
    assert snapshot.asks[0].price == Decimal("101")


def test_bybit_order_book_topics() -> None:
    collector = BybitSpotOrderBookCollector(["BTCUSDT", "ETHUSDT"], depth=50)

    assert collector.topics == (
        "orderbook.50.BTCUSDT",
        "orderbook.50.ETHUSDT",
    )
