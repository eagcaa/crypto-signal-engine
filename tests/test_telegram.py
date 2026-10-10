from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from crypto_signal_engine.integrations.telegram import TelegramNotifier
from crypto_signal_engine.paper.models import PaperPosition, PaperPositionStatus
from crypto_signal_engine.predictions import (
    Prediction,
    PredictionDirection,
    PredictionEvaluation,
    PredictionEvaluationOutcome,
    PredictionEvaluationStatus,
)


def make_prediction(
    *,
    take_profit_pct: Decimal = Decimal("0.40"),
    stop_loss_pct: Decimal = Decimal("0.30"),
) -> Prediction:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.31"),
        data_quality=Decimal("0.83"),
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        model_name="composite_rules_v3_5m",
        reason="trend=+0.100",
    )


def test_telegram_notifier_requires_credentials() -> None:
    with pytest.raises(ValueError):
        TelegramNotifier("", "123")

    with pytest.raises(ValueError):
        TelegramNotifier("token", "")


def test_prediction_text_does_not_invent_confidence() -> None:
    text = TelegramNotifier.prediction_text(make_prediction())

    assert "LONG" in text
    assert "Confidence: not calibrated" in text


def test_prediction_text_uses_calibrated_confidence_when_supplied() -> None:
    text = TelegramNotifier.prediction_text(
        make_prediction(),
        calibrated_confidence=Decimal("68.42"),
    )

    assert "Confidence: 68.42%" in text


def test_evaluation_text_formats_measured_outcome() -> None:
    prediction = make_prediction()
    evaluation = PredictionEvaluation(
        prediction_id=prediction.id,
        status=PredictionEvaluationStatus.EVALUATED,
        outcome=PredictionEvaluationOutcome.TAKE_PROFIT,
        label=1,
        evaluated_at=prediction.created_at + timedelta(seconds=30),
        exit_price=Decimal("100.60"),
        return_pct=Decimal("0.60"),
        success=True,
    )

    text = TelegramNotifier.evaluation_text(
        evaluation,
        symbol=prediction.symbol,
        horizon_seconds=prediction.horizon_seconds,
        direction=prediction.direction.value,
    )

    assert "Outcome: take_profit" in text
    assert "Return: +0.6000%" in text



def test_candidate_open_text_is_clearly_paper_only() -> None:
    prediction = make_prediction(
        take_profit_pct=Decimal("0.40"),
        stop_loss_pct=Decimal("0.18"),
    )

    text = TelegramNotifier.candidate_open_text(
        prediction,
        candidate_name="15m_long_range_high",
    )

    assert "EARLY CANDIDATE - PAPER ONLY" in text
    assert "15m_long_range_high" in text
    assert "TP: +0.4000%" in text
    assert "SL: -0.1800%" in text


def test_candidate_close_text_formats_result() -> None:
    prediction = make_prediction()
    position = PaperPosition(
        id=uuid4(),
        prediction_id=prediction.id,
        symbol="BTCUSDT",
        horizon_seconds=900,
        direction="long",
        opened_at=prediction.created_at,
        entry_price=Decimal("100"),
        notional=Decimal("1000"),
        quantity=Decimal("10"),
        model_name="candidate_paper_v5_15m_long_range_high",
        status=PaperPositionStatus.CLOSED,
        closed_at=prediction.created_at + timedelta(minutes=5),
        exit_price=Decimal("100.40"),
        return_pct=Decimal("0.28"),
        pnl=Decimal("2.80"),
        close_reason="take_profit",
    )

    text = TelegramNotifier.candidate_close_text(
        position,
        candidate_name="15m_long_range_high",
    )

    assert "CANDIDATE RESULT - PAPER ONLY" in text
    assert "Result: take_profit" in text
    assert "Return: +0.2800%" in text
    assert "PnL: +2.80" in text



def test_source_freshness_text_names_stale_source() -> None:
    text = TelegramNotifier.source_freshness_text(
        symbol="BTCUSDT",
        source="binance_futures",
        kind="stale",
        age_ms=45000,
        bad_intervals=3,
    )

    assert "Market source stale" in text
    assert "Binance Futures" in text
    assert "45000 ms" in text
