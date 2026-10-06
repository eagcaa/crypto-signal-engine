from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide, TradeTick
from crypto_signal_engine.features.cvd import CvdAccumulator


def test_cvd_accumulates_buy_minus_sell_volume() -> None:
    accumulator = CvdAccumulator()

    accumulator.update(
        TradeTick(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            symbol="BTCUSDT",
            event_time=datetime.now(UTC),
            price=Decimal("120000"),
            quantity=Decimal("2"),
            side=TradeSide.BUY,
        )
    )

    value = accumulator.update(
        TradeTick(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            symbol="BTCUSDT",
            event_time=datetime.now(UTC),
            price=Decimal("120010"),
            quantity=Decimal("0.75"),
            side=TradeSide.SELL,
        )
    )

    assert value == Decimal("1.25")
