from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.features.technical import build_technical_features


def make_candles(*, rising: bool) -> list[Candle]:
    start = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
    candles: list[Candle] = []

    for index in range(80):
        base = Decimal("100")
        move = Decimal(index) * Decimal("0.20")
        close = base + move if rising else base - move
        open_price = close - Decimal("0.05") if rising else close + Decimal("0.05")

        candles.append(
            Candle(
                symbol="BTCUSDT",
                interval="5m",
                open_time=start + timedelta(minutes=index * 5),
                close_time=start + timedelta(minutes=(index + 1) * 5),
                open=open_price,
                high=max(open_price, close) + Decimal("0.20"),
                low=min(open_price, close) - Decimal("0.20"),
                close=close,
                volume=Decimal("10"),
            )
        )

    return candles


def test_technical_features_detect_uptrend() -> None:
    features = build_technical_features(make_candles(rising=True))

    assert features.trend_score > 0
    assert features.trend_regime == "uptrend"
    assert features.atr > 0
    assert features.atr_pct > 0


def test_technical_features_detect_downtrend() -> None:
    features = build_technical_features(make_candles(rising=False))

    assert features.trend_score < 0
    assert features.trend_regime == "downtrend"


def test_technical_features_require_history() -> None:
    try:
        build_technical_features(make_candles(rising=True)[:10])
    except ValueError as exc:
        assert "closed candles" in str(exc)
    else:
        raise AssertionError("Expected insufficient-history ValueError")
