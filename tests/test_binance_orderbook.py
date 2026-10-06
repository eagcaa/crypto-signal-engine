from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.collectors.binance.orderbook import (
    BinanceSpotOrderBookCollector,
    parse_depth_snapshot,
)


def test_parse_binance_depth_snapshot() -> None:
    snapshot = parse_depth_snapshot(
        {
            "bids": [["100", "2"], ["99", "3"]],
            "asks": [["101", "4"], ["102", "5"]],
        },
        symbol="BTCUSDT",
        event_time=datetime.now(UTC),
    )

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.bids[0].price == Decimal("100")
    assert snapshot.asks[0].quantity == Decimal("4")


def test_binance_order_book_combined_stream() -> None:
    collector = BinanceSpotOrderBookCollector(["BTCUSDT", "ETHUSDT"])

    assert collector._build_url() == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@depth20@100ms/ethusdt@depth20@100ms"
    )
