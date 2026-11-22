"""Causal ridge-regression price research with walk-forward baseline validation.

Uses only closed one-minute candles. This is not a trading signal. No orderbook
history is assumed: a current snapshot must never be inserted into past training.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import log, sqrt

from crypto_signal_engine.domain.candles import Candle

D = Decimal
FEATURES = 8
MIN_TRAIN = 240


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


def _feature_vector(candles: list[Candle], i: int) -> list[float]:
    close = [float(c.close) for c in candles[i - 30:i + 1]]
    p = close[-1]
    returns = [close[j] / close[j - 1] - 1 for j in range(11, 31)]
    mean = sum(returns) / len(returns)
    volatility = sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))
    vols = [float(c.volume) for c in candles[i - 20:i]]
    avg_vol = sum(vols) / len(vols)
    latest = candles[i]
    spread = float(latest.high - latest.low)
    return [
        (p / close[-2] - 1) * 100,
        (p / close[-6] - 1) * 100,
        (p / close[-16] - 1) * 100,
        (p / close[0] - 1) * 100,
        volatility * 100,
        (float(latest.volume) / avg_vol - 1) if avg_vol > 0 else 0,
        (float(latest.close - latest.open) / spread) if spread > 0 else 0,
        (float(latest.high - latest.low) / p) * 100,
    ]


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    a = [row[:] + [rhs[k]] for k, row in enumerate(matrix)]
    for k in range(n):
        pivot = max(range(k, n), key=lambda j: abs(a[j][k]))
        a[k], a[pivot] = a[pivot], a[k]
        if abs(a[k][k]) < 1e-12:
            raise ValueError("Singular regression matrix")
        scale = a[k][k]
        for j in range(k, n + 1):
            a[k][j] /= scale
        for i in range(n):
            if i == k:
                continue
            factor = a[i][k]
            for j in range(k, n + 1):
                a[i][j] -= factor * a[k][j]
    return [a[i][n] for i in range(n)]


def _fit(candles: list[Candle], horizon: int, prediction_index: int):
    # The newest training label must have matured at prediction_index.
    end = prediction_index - horizon
    indices = range(30, end + 1)
    if len(indices) < MIN_TRAIN:
        raise ValueError("Not enough matured training examples")
    x = [_feature_vector(candles, i) for i in indices]
    y = [log(float(candles[i + horizon].close / candles[i].close)) * 100
         for i in indices]
    means = [sum(row[k] for row in x) / len(x) for k in range(FEATURES)]
    stds = [
        max(sqrt(sum((row[k] - means[k]) ** 2 for row in x) / len(x)), 1e-8)
        for k in range(FEATURES)
    ]
    normalized = [[1.0] + [(row[k] - means[k]) / stds[k]
                           for k in range(FEATURES)] for row in x]
    n = FEATURES + 1
    matrix = [[sum(row[a] * row[b] for row in normalized) / len(x)
               for b in range(n)] for a in range(n)]
    rhs = [sum(row[a] * target for row, target in zip(normalized, y)) / len(x)
           for a in range(n)]
    # Regularization prevents unstable coefficients on correlated indicators.
    for k in range(1, n):
        matrix[k][k] += 10.0
    coefficients = _solve(matrix, rhs)
    return coefficients, means, stds


def _estimate(candles: list[Candle], i: int, fitted) -> float:
    coefficients, means, stds = fitted
    features = _feature_vector(candles, i)
    predicted_log_pct = coefficients[0] + sum(
        coefficients[k + 1] * (features[k] - means[k]) / stds[k]
        for k in range(FEATURES)
    )
    # Conservative clamp: never report an extreme extrapolation as a forecast.
    predicted_log_pct = max(-3.0, min(3.0, predicted_log_pct))
    from math import exp
    return float(candles[i].close) * exp(predicted_log_pct / 100)


def predict_prices(candles: list[Candle], horizons=(5, 15),
                   validation_points: int = 100) -> list[PricePrediction]:
    if len(candles) < 400:
        raise ValueError("At least 400 closed 1m candles required")
    if any(c.interval != "1m" or c.symbol != candles[0].symbol or not c.closed
           for c in candles):
        raise ValueError("Expected closed single-symbol 1m candles")
    if any((b.open_time - a.open_time).total_seconds() != 60
           for a, b in zip(candles, candles[1:])):
        raise ValueError("Discontinuous candles")
    if any(c.close <= 0 or c.high < max(c.open, c.close)
           or c.low > min(c.open, c.close) or c.volume < 0 for c in candles):
        raise ValueError("Invalid candle prices or volumes")
    if validation_points < 50:
        raise ValueError("At least 50 validation points required")
    last = len(candles) - 1
    result = []
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("Horizon must be positive")
        validation_end = last - horizon
        validation_start = max(30 + MIN_TRAIN + horizon - 1,
                               validation_end - validation_points + 1)
        model_errors, naive_errors = [], []
        # Refitting every 10 samples, always using only labels matured at the
        # FIRST prediction in that block. Never train on validation futures.
        fitted = None
        for i in range(validation_start, validation_end + 1):
            if fitted is None or (i - validation_start) % 10 == 0:
                fitted = _fit(candles, horizon, i)
            estimate = _estimate(candles, i, fitted)
            actual = float(candles[i + horizon].close)
            model_errors.append(abs(estimate - actual) / actual * 100)
            naive_errors.append(abs(float(candles[i].close) - actual) / actual * 100)
        if len(model_errors) < 50:
            raise ValueError("Not enough out-of-sample validation points")
        model_mae = sum(model_errors) / len(model_errors)
        naive_mae = sum(naive_errors) / len(naive_errors)
        beats_naive = model_mae < naive_mae
        # Suppress unsupported model targets; expose baseline instead.
        estimate = (D(str(_estimate(candles, last, _fit(candles, horizon, last))))
                    if beats_naive else candles[-1].close)
        current = candles[-1].close
        result.append(PricePrediction(
            candles[-1].symbol, candles[-1].close_time.isoformat(),
            current, horizon, estimate, (estimate / current - 1) * 100,
            D(str(model_mae)), D(str(naive_mae)),
            beats_naive, len(model_errors)))
    return result
