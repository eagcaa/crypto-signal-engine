from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class PredictionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class PredictionDecisionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"


class PredictionEvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    EXPIRED_WITHOUT_DATA = "expired_without_data"


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
    feature_contributions: dict[str, Decimal] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PredictionDecision:
    symbol: str
    timestamp: datetime
    horizon_seconds: int
    direction: PredictionDecisionDirection
    raw_score: Decimal
    feature_contributions: dict[str, Decimal]
    reason: str
    prediction: Prediction | None


@dataclass(frozen=True, slots=True)
class PredictionEvaluation:
    prediction_id: UUID
    status: PredictionEvaluationStatus
    evaluated_at: datetime
    exit_price: Decimal | None
    return_pct: Decimal | None
    success: bool | None
