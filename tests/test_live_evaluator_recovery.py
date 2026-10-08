import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide, TradeTick
from crypto_signal_engine.predictions import (
    LiveFirstTouchEvaluator,
    Prediction,
    PredictionDirection,
)


def test_register_many_restores_open_prediction_for_tick_evaluation() -> None:
    async def run() -> None:
        created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        prediction = Prediction(
            id=uuid4(),
            symbol="BTCUSDT",
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=5),
            horizon_seconds=300,
            direction=PredictionDirection.LONG,
            entry_price=Decimal("100"),
            raw_score=Decimal("0.4"),
            data_quality=Decimal("0.83"),
            model_name="composite_rules_v3_5m",
        )

        evaluator = LiveFirstTouchEvaluator()
        restored = await evaluator.register_many([prediction])

        assert restored == 1
        assert await evaluator.open_count() == 1

        evaluations = await evaluator.process_trade(
            TradeTick(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                symbol="BTCUSDT",
                event_time=created_at + timedelta(seconds=10),
                price=Decimal("100.61"),
                quantity=Decimal("1"),
                side=TradeSide.BUY,
            )
        )

        assert len(evaluations) == 1
        assert evaluations[0].outcome.value == "take_profit"
        assert evaluations[0].prediction_id == prediction.id
        assert await evaluator.open_count() == 0

    asyncio.run(run())


def test_register_many_does_not_evaluate_on_noncanonical_trade() -> None:
    async def run() -> None:
        created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        prediction = Prediction(
            id=uuid4(),
            symbol="BTCUSDT",
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=5),
            horizon_seconds=300,
            direction=PredictionDirection.SHORT,
            entry_price=Decimal("100"),
            raw_score=Decimal("-0.4"),
            data_quality=Decimal("0.83"),
        )

        evaluator = LiveFirstTouchEvaluator()
        await evaluator.register_many([prediction])

        evaluations = await evaluator.process_trade(
            TradeTick(
                exchange=Exchange.BYBIT,
                market_type=MarketType.SPOT,
                symbol="BTCUSDT",
                event_time=created_at + timedelta(seconds=10),
                price=Decimal("99"),
                quantity=Decimal("1"),
                side=TradeSide.SELL,
            )
        )

        assert evaluations == []
        assert await evaluator.open_count() == 1

    asyncio.run(run())
