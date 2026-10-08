from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.replay import ReplayPricePoint, ReplayRunner, build_replay_report


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
        atr_pct_5m=Decimal("0.4"),
        atr_pct_15m=Decimal("0.6"),
        trend_regime_5m="uptrend",
        trend_regime_15m="uptrend",
        volatility_regime_5m="high",
        volatility_regime_15m="normal",
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


def test_report_groups_results_by_horizon_and_regime() -> None:
    started_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    features = [make_features(started_at)]

    result = ReplayRunner(
        horizons=(300, 900),
        prediction_interval_seconds=60,
    ).run(
        features,
        [
            ReplayPricePoint(
                symbol="BTCUSDT",
                timestamp=started_at + timedelta(seconds=20),
                price=Decimal("100.61"),
            )
        ],
    )

    report = build_replay_report(result, features)

    assert report.by_horizon[300].predictions == 1
    assert report.by_horizon[300].take_profit == 1
    assert report.by_horizon[300].tp_rate == Decimal("100")

    five_minute = next(
        item for item in report.by_regime if item.horizon_seconds == 300
    )
    assert five_minute.direction == "long"
    assert five_minute.trend_regime == "uptrend"
    assert five_minute.volatility_regime == "high"
    assert five_minute.stats.take_profit == 1


def test_report_counts_no_trade_decisions_separately() -> None:
    started_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    low_quality = replace(
        make_features(started_at),
        market_data_quality=Decimal("0.20"),
    )

    result = ReplayRunner(horizons=(300,)).run([low_quality], [])
    report = build_replay_report(result, [low_quality])

    assert result.no_trade_count == 1
    assert not result.predictions
    assert not report.by_horizon
