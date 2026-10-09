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


class PredictionEvaluationOutcome(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    EXPIRED_NO_TOUCH = "expired_no_touch"
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
    take_profit_pct: Decimal = Decimal("0.60")
    stop_loss_pct: Decimal = Decimal("0.30")
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
    outcome: PredictionEvaluationOutcome
    label: int | None
    evaluated_at: datetime
    exit_price: Decimal | None
    return_pct: Decimal | None
    success: bool | None
    evaluation_source: str = "unknown"
    evaluation_version: str = "first_touch_v1"
