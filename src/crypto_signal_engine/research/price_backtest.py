"""Strict chronological train/validation/test evaluation of 1m price forecasts."""
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.historical_price_forecast import _estimate, _fit


def read_candles(path: Path) -> list[Candle]:
    candles = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            candles.append(Candle(
                symbol=row["symbol"], interval=row["interval"],
                open_time=datetime.fromisoformat(row["open_time"]),
                close_time=datetime.fromisoformat(row["close_time"]),
                open=Decimal(row["open"]), high=Decimal(row["high"]),
                low=Decimal(row["low"]), close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                quote_volume=Decimal(row["quote_volume"])))
    if not candles:
        raise ValueError("Empty history")
    if any((b.open_time - a.open_time).total_seconds() != 60
           for a, b in zip(candles, candles[1:])):
        raise ValueError("Missing minute in history")
    return candles


def _fit_at(candles: list[Candle], horizon: int, cutoff: int):
    # Bounded rolling training window: 3,000 rows, with only matured labels.
    start = max(0, cutoff - 3000)
    subset = candles[start:cutoff + 1]
    return subset, _fit(subset, horizon, len(subset) - 1)


def _score(candles, fitted, indices, horizon):
    subset, model = fitted
    # _estimate needs the same candle history as training for feature indexing;
    # append only historical candles up to the observation point.
    # Coefficients and scalers are fixed, no refit on the scored segment.
    errors, naive_errors, directions = [], [], []
    for i in indices:
        observed = candles[:i + 1]
        estimated = _estimate(observed, i, model)
        current = float(candles[i].close)
        actual = float(candles[i + horizon].close)
        errors.append(abs(estimated - actual) / actual * 100)
        naive_errors.append(abs(current - actual) / actual * 100)
        directions.append((estimated > current) == (actual > current)
                          if estimated != current and actual != current else False)
    return (sum(errors) / len(errors), sum(naive_errors) / len(errors),
            100 * sum(directions) / len(directions), len(errors))


def _sample(start, stop, count=300):
    if stop <= start:
        raise ValueError("Not enough evaluation observations")
    step = max(1, (stop - start) // count)
    return range(start, stop, step)


def evaluate(candles: list[Candle], horizons=(5, 15)) -> list[dict]:
    if len(candles) < 2000:
        raise ValueError("At least 2000 consecutive 1m candles required")
    n = len(candles)
    train_end = int(n * .6)
    validation_end = int(n * .8)
    results = []
    for horizon in horizons:
        # Train on first 60%, validate on next 20%; never use test data
        # to decide which model is acceptable.
        first_fit = _fit_at(candles, horizon, train_end - 1)
        val = _score(candles, first_fit,
                     _sample(train_end, validation_end - horizon), horizon)
        validation_pass = val[0] < val[1]
        # Fit on train+validation, using matured labels only. Last 20% untouched.
        final_fit = _fit_at(candles, horizon, validation_end - 1)
        test = _score(candles, final_fit,
                      _sample(validation_end, n - horizon), horizon)
        test_pass = test[0] < test[1]
        results.append({
            "horizon": horizon,
            "validation_model_mae": val[0],
            "validation_naive_mae": val[1],
            "test_model_mae": test[0],
            "test_naive_mae": test[1],
            "test_direction_accuracy": test[2],
            "test_n": test[3],
            "status": ("PASS" if validation_pass and test_pass
                       else "FAIL_NO_EDGE"),
        })
    return results
