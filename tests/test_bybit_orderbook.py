from decimal import Decimal

from crypto_signal_engine.collectors.bybit.orderbook import (
    BybitLocalOrderBook,
    BybitSpotOrderBookCollector,
)


def test_bybit_order_book_topics() -> None:
    collector = BybitSpotOrderBookCollector(["BTCUSDT", "ETHUSDT"], depth=50)

    assert collector.topics == (
        "orderbook.50.BTCUSDT",
        "orderbook.50.ETHUSDT",
    )


def test_local_book_merges_snapshot_and_delta() -> None:
    book = BybitLocalOrderBook(depth=50)

    snapshot = book.apply(
        {
            "type": "snapshot",
            "ts": 1720000000123,
            "data": {
                "b": [["100", "2"], ["99", "3"]],
                "a": [["101", "4"], ["102", "5"]],
            },
        },
        symbol="BTCUSDT",
    )

    assert snapshot is not None
    assert snapshot.bids[0].price == Decimal("100")
    assert snapshot.asks[0].price == Decimal("101")

    updated = book.apply(
        {
            "type": "delta",
            "ts": 1720000001123,
            "data": {
                "b": [["100", "0"], ["98", "7"]],
                "a": [["101", "6"]],
            },
        },
        symbol="BTCUSDT",
    )

    assert updated is not None
    assert updated.bids[0].price == Decimal("99")
    assert updated.bids[1].price == Decimal("98")
    assert updated.asks[0].price == Decimal("101")
    assert updated.asks[0].quantity == Decimal("6")


def test_delta_before_snapshot_is_ignored() -> None:
    book = BybitLocalOrderBook(depth=50)

    result = book.apply(
        {
            "type": "delta",
            "ts": 1720000001123,
            "data": {
                "b": [["100", "2"]],
                "a": [],
            },
        },
        symbol="BTCUSDT",
    )

    assert result is None
