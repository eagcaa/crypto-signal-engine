"""Historical-analogue point forecasts with rolling out-of-sample baseline check.

Research only. No model is assumed accurate without validation.
"""
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from crypto_signal_engine.domain.candles import Candle

D = Decimal


@dataclass(frozen=True)
class PricePrediction:
    symbol: str
    observed_at: str
    current: Decimal
    horizon_minutes: int
    estimate: Decimal
    change_pct: Decimal
    model_mae_pct: Decimal
    naive_mae_pct: Decimal
    beats_naive: bool
    validation_count: int


def _features(closes: list[float], index: int) -> tuple[float, float]:
    return (closes[index] / closes[index - 5] - 1,
            closes[index] / closes[index - 15] - 1)


def _predict_return(closes: list[float], index: int, horizon: int,
                    max_candidate_index: int, neighbors: int = 30) -> float:
    target = _features(closes, index)
    matches = []
    # Historical candidates have known outcomes before the prediction timestamp.
    for j in range(15, max_candidate_index + 1):
        distance = abs(_features(closes, j)[0] - target[0]) + abs(
            _features(closes, j)[1] - target[1])
        matches.append((distance, j))
    matches.sort()
    if len(matches) < neighbors:
        raise ValueError("Insufficient historical analogues")
    returns = [closes[j + horizon] / closes[j] - 1
               for _, j in matches[:neighbors]]
    return median(returns)


def predict_prices(candles: list[Candle], horizons=(5, 15),
                   validation_points: int = 100) -> list[PricePrediction]:
    if len(candles) < 300:
        raise ValueError("At least 300 closed 1m candles required")
    if any(c.interval != "1m" or c.symbol != candles[0].symbol or not c.closed
           for c in candles):
        raise ValueError("Expected closed single-symbol 1m candles")
    if any((b.open_time - a.open_time).total_seconds() != 60
           for a, b in zip(candles, candles[1:])):
        raise ValueError("Discontinuous candles")
    closes = [float(c.close) for c in candles]
    if any(p <= 0 for p in closes):
        raise ValueError("Nonpositive price")
    result = []
    last = len(closes) - 1
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("Horizon must be positive")
        # Each validation prediction only uses data available at that time;
        # outcomes from historical candidates must have completed already.
        validation_end = last - horizon
        validation_start = max(150, validation_end - validation_points + 1)
        model_errors, naive_errors = [], []
        for i in range(validation_start, validation_end + 1):
            try:
                estimated_return = _predict_return(closes, i, horizon, i - horizon)
            except ValueError:
                continue
            actual = closes[i + horizon]
            model_errors.append(abs(closes[i] * (1 + estimated_return) - actual) / actual * 100)
            naive_errors.append(abs(closes[i] - actual) / actual * 100)
        if len(model_errors) < 50:
            raise ValueError("Not enough out-of-sample validation points")
        predicted_return = _predict_return(closes, last, horizon, last - horizon)
        estimate = D(str(closes[last] * (1 + predicted_return)))
        current = candles[-1].close
        model_mae = sum(model_errors) / len(model_errors)
        naive_mae = sum(naive_errors) / len(naive_errors)
        result.append(PricePrediction(
            candles[-1].symbol, candles[-1].close_time.isoformat(),
            current, horizon, estimate, (estimate / current - 1) * 100,
            D(str(model_mae)), D(str(naive_mae)),
            model_mae < naive_mae, len(model_errors)))
    return result
