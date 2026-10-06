from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.market import MarketSnapshot
from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionDirection,
)


class BaselinePredictionEngine:
    """Research-only baseline signal generator.

    The raw score is NOT a calibrated probability or confidence percentage.
    It currently uses the mean spot order-book imbalance across Binance/Bybit.
    """

    def __init__(
        self,
        *,
        minimum_data_quality: Decimal = Decimal("0.80"),
        minimum_abs_score: Decimal = Decimal("0.30"),
    ) -> None:
        self._minimum_data_quality = minimum_data_quality
        self._minimum_abs_score = minimum_abs_score

    def generate(
        self,
        snapshot: MarketSnapshot,
        *,
        horizon_seconds: int,
    ) -> Prediction | None:
        if snapshot.price is None:
            return None

        if snapshot.data_quality < self._minimum_data_quality:
            return None

        if (
            snapshot.binance_book_imbalance is None
            or snapshot.bybit_book_imbalance is None
        ):
            return None

        raw_score = (
            snapshot.binance_book_imbalance
            + snapshot.bybit_book_imbalance
        ) / Decimal("2")

        if abs(raw_score) < self._minimum_abs_score:
            return None

        direction = (
            PredictionDirection.LONG
            if raw_score > 0
            else PredictionDirection.SHORT
        )

        return Prediction(
            id=uuid4(),
            symbol=snapshot.symbol,
            created_at=snapshot.timestamp,
            expires_at=snapshot.timestamp + timedelta(seconds=horizon_seconds),
            horizon_seconds=horizon_seconds,
            direction=direction,
            entry_price=snapshot.price,
            raw_score=raw_score,
            data_quality=snapshot.data_quality,
        )
