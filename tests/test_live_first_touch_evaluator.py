import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    TradeSide,
    TradeTick,
)
from crypto_signal_engine.predictions import (
    LiveFirstTouchEvaluator,
    Prediction,
    PredictionDirection,
    PredictionEvaluationOutcome,
)


def make_prediction(
    *,
    direction: PredictionDirection,
) -> Prediction:
    created_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
        horizon_seconds=300,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.4"),
        data_quality=Decimal("0.83"),
        model_name="test",
    )


def make_trade(
    prediction: Prediction,
    *,
    seconds: int,
    price: str,
    exchange: Exchange = Exchange.BINANCE,
    market_type: MarketType = MarketType.SPOT,
) -> TradeTick:
    return TradeTick(
        exchange=exchange,
        market_type=market_type,
        symbol=prediction.symbol,
        event_time=prediction.created_at + timedelta(seconds=seconds),
        price=Decimal(price),
        quantity=Decimal("1"),
        side=TradeSide.BUY,
    )


def test_live_tick_evaluator_catches_long_tp_between_snapshots() -> None:
    async def run() -> None:
        prediction = make_prediction(direction=PredictionDirection.LONG)
        evaluator = LiveFirstTouchEvaluator()
        await evaluator.register(prediction)

        no_touch = await evaluator.process_trade(
            make_trade(prediction, seconds=1, price="100.20")
        )
        hit = await evaluator.process_trade(
            make_trade(prediction, seconds=2, price="100.61")
        )
        duplicate = await evaluator.process_trade(
            make_trade(prediction, seconds=3, price="100.70")
        )

        assert no_touch == []
        assert len(hit) == 1
        assert hit[0].outcome == PredictionEvaluationOutcome.TAKE_PROFIT
        assert hit[0].label == 1
        assert hit[0].evaluated_at == prediction.created_at + timedelta(seconds=2)
        assert duplicate == []

    asyncio.run(run())


def test_live_tick_evaluator_catches_short_stop_loss() -> None:
    async def run() -> None:
        prediction = make_prediction(direction=PredictionDirection.SHORT)
        evaluator = LiveFirstTouchEvaluator()
        await evaluator.register(prediction)

        hit = await evaluator.process_trade(
            make_trade(prediction, seconds=2, price="100.31")
        )

        assert len(hit) == 1
        assert hit[0].outcome == PredictionEvaluationOutcome.STOP_LOSS
        assert hit[0].label == -1
        assert hit[0].success is False

    asyncio.run(run())


def test_live_tick_evaluator_uses_only_binance_spot_as_reference() -> None:
    async def run() -> None:
        prediction = make_prediction(direction=PredictionDirection.LONG)
        evaluator = LiveFirstTouchEvaluator()
        await evaluator.register(prediction)

        bybit_hit = await evaluator.process_trade(
            make_trade(
                prediction,
                seconds=1,
                price="100.70",
                exchange=Exchange.BYBIT,
            )
        )
        futures_hit = await evaluator.process_trade(
            make_trade(
                prediction,
                seconds=2,
                price="100.70",
                market_type=MarketType.FUTURES,
            )
        )
        binance_spot_hit = await evaluator.process_trade(
            make_trade(prediction, seconds=3, price="100.70")
        )

        assert bybit_hit == []
        assert futures_hit == []
        assert len(binance_spot_hit) == 1

    asyncio.run(run())
