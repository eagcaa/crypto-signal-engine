from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.replay import ReplayPricePoint, ReplayRunner


def make_features(timestamp: datetime) -> ResearchFeatureSnapshot:
    return ResearchFeatureSnapshot(
        symbol="BTCUSDT",
        timestamp=timestamp,
        price=Decimal("100"),
        spot_cvd_1m=Decimal("1"),
        spot_cvd_5m=Decimal("1"),
        spot_cvd_15m=Decimal("1"),
        futures_cvd_1m=Decimal("1"),
        futures_cvd_5m=Decimal("1"),
        futures_cvd_15m=Decimal("1"),
        spot_cvd_ratio_1m=Decimal("0.8"),
        spot_cvd_ratio_5m=Decimal("0.8"),
        spot_cvd_ratio_15m=Decimal("0.8"),
        futures_cvd_ratio_1m=Decimal("0.8"),
        futures_cvd_ratio_5m=Decimal("0.8"),
        futures_cvd_ratio_15m=Decimal("0.8"),
        spot_trade_sources=2,
        futures_trade_sources=2,
        history_seconds=900,
        trend_score_5m=Decimal("0.8"),
        trend_score_15m=Decimal("0.8"),
        trend_score_1h=None,
        trend_score_4h=None,
        atr_pct_5m=Decimal("0.4"),
        atr_pct_15m=Decimal("0.6"),
        atr_pct_1h=None,
        atr_pct_4h=None,
        trend_regime_5m="uptrend",
        trend_regime_15m="uptrend",
        trend_regime_1h=None,
        trend_regime_4h=None,
        volatility_regime_5m="normal",
        volatility_regime_15m="normal",
        volatility_regime_1h=None,
        volatility_regime_4h=None,
        binance_oi_change_5m_pct=Decimal("0.4"),
        binance_oi_change_15m_pct=Decimal("0.4"),
        bybit_oi_change_5m_pct=Decimal("0.4"),
        bybit_oi_change_15m_pct=Decimal("0.4"),
        binance_funding_rate=Decimal("-0.0001"),
        bybit_funding_rate=Decimal("-0.0001"),
        binance_long_short_ratio=Decimal("0.9"),
        bybit_long_short_ratio=Decimal("0.9"),
        binance_top_trader_long_short_ratio=Decimal("0.9"),
        binance_taker_buy_sell_ratio=Decimal("1.5"),
        long_liquidations_5m_usd=Decimal("10000"),
        short_liquidations_5m_usd=Decimal("30000"),
        liquidation_imbalance_5m=Decimal("0.5"),
        long_liquidations_15m_usd=Decimal("10000"),
        short_liquidations_15m_usd=Decimal("30000"),
        liquidation_imbalance_15m=Decimal("0.5"),
        binance_book_imbalance=Decimal("0.8"),
        bybit_book_imbalance=Decimal("0.6"),
        market_data_quality=Decimal("0.83"),
    )


def test_replay_uses_same_engine_and_detects_first_touch() -> None:
    started_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    features = make_features(started_at)

    result = ReplayRunner(
        horizons=(300,),
        prediction_interval_seconds=60,
    ).run(
        [features],
        [
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=10),
                price=Decimal("100.20"),
            ),
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=20),
                price=Decimal("100.61"),
            ),
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=30),
                price=Decimal("99.00"),
            ),
        ],
    )

    assert len(result.predictions) == 1
    assert result.evaluated_count == 1
    assert result.evaluations[0].outcome.value == "take_profit"
    assert result.evaluations[0].label == 1
    assert result.evaluations[0].evaluated_at == started_at + timedelta(seconds=20)
    assert not result.open_predictions


def test_replay_expires_without_touch_and_keeps_directional_return() -> None:
    started_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    features = make_features(started_at)

    result = ReplayRunner(
        horizons=(300,),
        prediction_interval_seconds=60,
    ).run(
        [features],
        [
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=120),
                price=Decimal("100.10"),
            ),
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=301),
                price=Decimal("100.15"),
            ),
        ],
    )

    assert result.evaluated_count == 1
    evaluation = result.evaluations[0]
    assert evaluation.outcome.value == "expired_no_touch"
    assert evaluation.label == 0
    assert evaluation.exit_price == Decimal("100.10")
    assert evaluation.return_pct == Decimal("0.100")


def test_replay_leaves_not_yet_expired_prediction_open() -> None:
    started_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    features = replace(
        make_features(started_at),
        price=Decimal("100"),
    )

    result = ReplayRunner(
        horizons=(300,),
        prediction_interval_seconds=60,
    ).run(
        [features],
        [
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=60),
                price=Decimal("100.10"),
            )
        ],
    )

    assert len(result.predictions) == 1
    assert not result.evaluations
    assert len(result.open_predictions) == 1
