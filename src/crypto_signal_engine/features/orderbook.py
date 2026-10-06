from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.domain.models import OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class OrderBookMetrics:
    bid_volume: Decimal
    ask_volume: Decimal
    imbalance: Decimal
    best_bid: Decimal
    best_ask: Decimal
    spread: Decimal


def calculate_order_book_metrics(
    snapshot: OrderBookSnapshot,
    *,
    depth_levels: int = 20,
) -> OrderBookMetrics:
    """Calculate simple top-of-book depth metrics.

    imbalance is normalized to [-1, 1]:
      +1 => entirely bid-heavy
      -1 => entirely ask-heavy
       0 => balanced
    """
    bids = snapshot.bids[:depth_levels]
    asks = snapshot.asks[:depth_levels]

    if not bids or not asks:
        raise ValueError("Order book snapshot must contain bids and asks")

    bid_volume = sum((level.quantity for level in bids), start=Decimal("0"))
    ask_volume = sum((level.quantity for level in asks), start=Decimal("0"))
    total = bid_volume + ask_volume

    imbalance = Decimal("0") if total == 0 else (bid_volume - ask_volume) / total

    best_bid = bids[0].price
    best_ask = asks[0].price

    return OrderBookMetrics(
        bid_volume=bid_volume,
        ask_volume=ask_volume,
        imbalance=imbalance,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=best_ask - best_bid,
    )
