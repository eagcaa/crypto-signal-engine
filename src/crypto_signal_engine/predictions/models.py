from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class PredictionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class Prediction:
    id: UUID
    symbol: str
    created_at: datetime
    expires_at: datetime
    horizon_seconds: int
    direction: PredictionDirection
    entry_price: Decimal
    raw_score: Decimal
    data_quality: Decimal
    model_name: str = "baseline_orderbook_v1"


@dataclass(frozen=True, slots=True)
class PredictionEvaluation:
    prediction_id: UUID
    evaluated_at: datetime
    exit_price: Decimal
    return_pct: Decimal
    success: bool
