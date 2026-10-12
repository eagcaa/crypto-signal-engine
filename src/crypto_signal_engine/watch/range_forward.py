"""Persist and evaluate live range forecasts without directional claims."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.collectors.binance import BinanceSpotCandleClient
from crypto_signal_engine.db.range_forecast_repository import (
    RangeForecastRecord,
    RangeForecastRepository,
)
from crypto_signal_engine.watch.coin_analysis import CoinAnalysis


_INTERVALS = {
    15: "1m",
    60: "5m",
    240: "15m",
    1440: "1h",
}


class RangeForwardTracker:
    def __init__(
        self,
        repository: RangeForecastRepository,
        *,
        candle_client: BinanceSpotCandleClient | None = None,
    ) -> None:
        self._repository = repository
        self._candles = candle_client or BinanceSpotCandleClient()

    async def persist_analysis(self, analysis: CoinAnalysis) -> None:
        request_id = uuid4()
        payload: list[dict[str, object]] = []
        for forecast in analysis.forecasts:
            payload.append(
                {
                    "anchor_at": forecast.anchor_at,
                    "anchor_price": forecast.anchor_price,
                    "horizon_minutes": forecast.horizon_minutes,
                    "target_coverage": forecast.target_coverage,
                    "lower_price": forecast.lower_price,
                    "upper_price": forecast.upper_price,
                    "calibration_count": forecast.calibration_count,
                    "volatility_scaled": forecast.volatility_scaled,
                    "due_at": forecast.anchor_at
                    + timedelta(minutes=forecast.horizon_minutes),
                }
            )

        inserted = await self._repository.add_many(
            request_id=request_id,
            symbol=analysis.symbol,
            generated_at=analysis.generated_at,
            forecasts=payload,
        )
        print(
            "RANGE_FORECAST_SAVED "
            f"symbol={analysis.symbol} request_id={request_id} rows={inserted}"
        )

    async def evaluate_due(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        # Allow exchange klines a short grace period to close and become visible.
        due = await self._repository.due_pending(
            now=now - timedelta(minutes=2),
            limit=100,
        )
        evaluated = 0
        for forecast in due:
            try:
                result = await self._evaluate_one(forecast)
            except Exception as exc:  # noqa: BLE001
                print(
                    "RANGE_FORECAST_EVAL unavailable "
                    f"id={forecast.id} symbol={forecast.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if result is None:
                # Keep it pending; a later evaluator pass may have the candles.
                continue

            observed_low, observed_high = result
            covered = (
                observed_low >= forecast.lower_price
                and observed_high <= forecast.upper_price
            )
            await self._repository.mark_evaluated(
                forecast.id,
                observed_low=observed_low,
                observed_high=observed_high,
                covered=covered,
                evaluated_at=now,
            )
            evaluated += 1
            print(
                "RANGE_FORECAST_EVALUATED "
                f"symbol={forecast.symbol} horizon_min={forecast.horizon_minutes} "
                f"covered={covered} observed_low={observed_low} "
                f"observed_high={observed_high} lower={forecast.lower_price} "
                f"upper={forecast.upper_price}"
            )
        return evaluated

    async def _evaluate_one(
        self,
        forecast: RangeForecastRecord,
    ) -> tuple[Decimal, Decimal] | None:
        interval = _INTERVALS.get(forecast.horizon_minutes)
        if interval is None:
            raise ValueError(
                f"Unsupported horizon: {forecast.horizon_minutes}"
            )

        interval_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "1h": 60,
        }[interval]
        limit = forecast.horizon_minutes // interval_minutes + 6
        candles = await self._candles.fetch_closed(
            forecast.symbol,
            interval=interval,
            limit=limit,
        )

        path = [
            candle
            for candle in candles
            if candle.close_time > forecast.anchor_at
            and candle.close_time <= forecast.due_at
        ]
        expected = forecast.horizon_minutes // interval_minutes
        if len(path) < expected:
            return None

        observed_low = min(candle.low for candle in path)
        observed_high = max(candle.high for candle in path)
        return observed_low, observed_high
