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

    async def mark_no_data(self, forecast_id, **kwargs):
        self.no_data = getattr(self, "no_data", []) + [forecast_id]


class FakeCandleClient:
    def __init__(self, candles) -> None:
        self.candles = candles

    async def fetch_closed(self, symbol, *, interval, limit=100, start_time=None):
        candles = self.candles
        if start_time is not None:
            candles = [item for item in candles if item.close_time > start_time]
            return candles[:limit]
        return candles[-limit:]


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



def _record(horizon: int = 15) -> RangeForecastRecord:
    return RangeForecastRecord(
        id=uuid4(),
        request_id=uuid4(),
        symbol="BTCUSDT",
        generated_at=T0,
        anchor_at=T0,
        anchor_price=Decimal("100"),
        horizon_minutes=horizon,
        target_coverage=Decimal("0.70"),
        lower_price=Decimal("99"),
        upper_price=Decimal("101"),
        calibration_count=50,
        volatility_scaled=True,
        due_at=T0 + timedelta(minutes=horizon),
        status="pending",
    )


def _flat_candles(minutes: range) -> list:
    return [
        SimpleNamespace(
            close_time=T0 + timedelta(minutes=minute),
            low=Decimal("99.50"),
            high=Decimal("100.50"),
        )
        for minute in minutes
    ]


@pytest.mark.asyncio
async def test_late_evaluator_still_sees_start_of_window() -> None:
    repository = FakeRepository()
    repository.due = [_record()]
    tracker = RangeForwardTracker(
        repository,
        candle_client=FakeCandleClient(_flat_candles(range(1, 300))),
    )

    assert await tracker.evaluate_due(T0 + timedelta(hours=5)) == 1
    _, values = repository.evaluated[0]
    assert values["covered"] is True


@pytest.mark.asyncio
async def test_incomplete_window_stays_pending_then_no_data() -> None:
    repository = FakeRepository()
    repository.due = [_record()]
    candles = _flat_candles(range(1, 8))
    tracker = RangeForwardTracker(
        repository,
        candle_client=FakeCandleClient(candles),
    )

    assert await tracker.evaluate_due(T0 + timedelta(minutes=20)) == 0
    assert repository.evaluated == []
    assert getattr(repository, "no_data", []) == []

    assert await tracker.evaluate_due(T0 + timedelta(hours=7)) == 0
    assert len(repository.no_data) == 1
