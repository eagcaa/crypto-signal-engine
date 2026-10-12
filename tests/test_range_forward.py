from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from crypto_signal_engine.db.range_forecast_repository import RangeForecastRecord
from crypto_signal_engine.watch.coin_analysis import CoinAnalysis, CoinRangeForecast
from crypto_signal_engine.watch.range_forward import RangeForwardTracker


T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.saved = None
        self.due = []
        self.evaluated = []

    async def add_many(self, **kwargs):
        self.saved = kwargs
        return len(kwargs["forecasts"])

    async def due_pending(self, *, now, limit=100):
        return list(self.due)

    async def mark_evaluated(self, forecast_id, **kwargs):
        self.evaluated.append((forecast_id, kwargs))


class FakeCandleClient:
    def __init__(self, candles) -> None:
        self.candles = candles

    async def fetch_closed(self, symbol, *, interval, limit=100):
        return self.candles[-limit:]


@pytest.mark.asyncio
async def test_range_tracker_persists_each_analysis_horizon() -> None:
    repository = FakeRepository()
    tracker = RangeForwardTracker(repository)
    analysis = CoinAnalysis(
        symbol="BTCUSDT",
        generated_at=T0,
        text="test",
        forecasts=(
            CoinRangeForecast(
                horizon_minutes=15,
                anchor_at=T0,
                anchor_price=100.0,
                target_coverage=0.70,
                lower_price=99.0,
                upper_price=101.0,
                calibration_count=50,
                volatility_scaled=True,
            ),
            CoinRangeForecast(
                horizon_minutes=60,
                anchor_at=T0,
                anchor_price=100.0,
                target_coverage=0.70,
                lower_price=98.0,
                upper_price=102.0,
                calibration_count=40,
                volatility_scaled=True,
            ),
        ),
    )

    await tracker.persist_analysis(analysis)

    assert repository.saved is not None
    assert repository.saved["symbol"] == "BTCUSDT"
    assert len(repository.saved["forecasts"]) == 2
    assert repository.saved["forecasts"][0]["due_at"] == T0 + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_range_tracker_uses_candle_high_low_for_full_path_coverage() -> None:
    forecast_id = uuid4()
    record = RangeForecastRecord(
        id=forecast_id,
        request_id=uuid4(),
        symbol="BTCUSDT",
        generated_at=T0,
        anchor_at=T0,
        anchor_price=Decimal("100"),
        horizon_minutes=15,
        target_coverage=Decimal("0.70"),
        lower_price=Decimal("99"),
        upper_price=Decimal("101"),
        calibration_count=50,
        volatility_scaled=True,
        due_at=T0 + timedelta(minutes=15),
        status="pending",
    )
    candles = []
    for minute in range(1, 16):
        close_time = T0 + timedelta(minutes=minute)
        candles.append(
            SimpleNamespace(
                close_time=close_time,
                low=Decimal("99.20"),
                high=Decimal("100.80") if minute != 7 else Decimal("101.20"),
            )
        )

    repository = FakeRepository()
    repository.due = [record]
    tracker = RangeForwardTracker(
        repository,
        candle_client=FakeCandleClient(candles),
    )

    count = await tracker.evaluate_due(T0 + timedelta(minutes=18))

    assert count == 1
    assert len(repository.evaluated) == 1
    _, values = repository.evaluated[0]
    assert values["observed_high"] == Decimal("101.20")
    assert values["covered"] is False
