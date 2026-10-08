from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionDecision,
    PredictionEvaluation,
)


@dataclass(frozen=True, slots=True)
class ReplayPricePoint:
    """Canonical price observation used for deterministic first-touch replay."""

    symbol: str
    timestamp: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class ReplayResult:
    decisions: tuple[PredictionDecision, ...]
    predictions: tuple[Prediction, ...]
    evaluations: tuple[PredictionEvaluation, ...]
    open_predictions: tuple[Prediction, ...]

    @property
    def no_trade_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.prediction is None)

    @property
    def evaluated_count(self) -> int:
        return len(self.evaluations)
