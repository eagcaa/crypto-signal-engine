from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.domain.models import TradeSide, TradeTick


@dataclass(slots=True)
class CvdAccumulator:
    buy_volume: Decimal = Decimal("0")
    sell_volume: Decimal = Decimal("0")

    @property
    def cvd(self) -> Decimal:
        return self.buy_volume - self.sell_volume

    def update(self, trade: TradeTick) -> Decimal:
        if trade.side is TradeSide.BUY:
            self.buy_volume += trade.quantity
        else:
            self.sell_volume += trade.quantity

        return self.cvd
