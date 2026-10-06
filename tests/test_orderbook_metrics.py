from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    OrderBookLevel,
    OrderBookSnapshot,
)
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics


def test_order_book_metrics_calculates_normalized_imbalance() -> None:
    snapshot = OrderBookSnapshot(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        event_time=datetime.now(UTC),
        bids=(
            OrderBookLevel(Decimal("100"), Decimal("6")),
            OrderBookLevel(Decimal("99"), Decimal("4")),
        ),
        asks=(
            OrderBookLevel(Decimal("101"), Decimal("3")),
            OrderBookLevel(Decimal("102"), Decimal("2")),
        ),
    )

    metrics = calculate_order_book_metrics(snapshot, depth_levels=2)

    assert metrics.bid_volume == Decimal("10")
    assert metrics.ask_volume == Decimal("5")
    assert metrics.imbalance == Decimal("5") / Decimal("15")
    assert metrics.best_bid == Decimal("100")
    assert metrics.best_ask == Decimal("101")
    assert metrics.spread == Decimal("1")
