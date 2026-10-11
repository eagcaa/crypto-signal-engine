from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.market.state_alerts import MarketStateAlertMonitor


def _snapshot() -> ResearchFeatureSnapshot:
    return ResearchFeatureSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        price=Decimal("100000"),
        spot_cvd_1m=Decimal("0"),
        spot_cvd_5m=Decimal("0"),
        spot_cvd_15m=Decimal("0"),
        futures_cvd_1m=Decimal("0"),
        futures_cvd_5m=Decimal("0"),
        futures_cvd_15m=Decimal("0"),
        spot_cvd_ratio_1m=Decimal("0"),
        spot_cvd_ratio_5m=Decimal("0"),
        spot_cvd_ratio_15m=Decimal("0"),
        futures_cvd_ratio_1m=Decimal("0"),
        futures_cvd_ratio_5m=Decimal("0"),
        futures_cvd_ratio_15m=Decimal("0"),
        spot_trade_sources=2,
        futures_trade_sources=2,
        history_seconds=900,
        trend_score_5m=Decimal("0"),
        trend_score_15m=Decimal("0"),
        trend_score_1h=Decimal("0"),
        trend_score_4h=Decimal("0"),
        atr_pct_5m=Decimal("0.5"),
        atr_pct_15m=Decimal("0.6"),
        atr_pct_1h=Decimal("1"),
        atr_pct_4h=Decimal("2"),
        trend_regime_5m="range",
        trend_regime_15m="range",
        trend_regime_1h="range",
        trend_regime_4h="range",
        volatility_regime_5m="normal",
        volatility_regime_15m="normal",
        volatility_regime_1h="normal",
        volatility_regime_4h="normal",
        binance_oi_change_5m_pct=Decimal("0"),
        binance_oi_change_15m_pct=Decimal("0"),
        bybit_oi_change_5m_pct=Decimal("0"),
        bybit_oi_change_15m_pct=Decimal("0"),
        binance_funding_rate=Decimal("0.0001"),
        bybit_funding_rate=Decimal("0.0001"),
        binance_long_short_ratio=Decimal("1"),
        bybit_long_short_ratio=Decimal("1"),
        binance_top_trader_long_short_ratio=Decimal("1"),
        binance_taker_buy_sell_ratio=Decimal("1"),
        long_liquidations_5m_usd=Decimal("0"),
        short_liquidations_5m_usd=Decimal("0"),
        liquidation_imbalance_5m=Decimal("0"),
        long_liquidations_15m_usd=Decimal("0"),
        short_liquidations_15m_usd=Decimal("0"),
        liquidation_imbalance_15m=Decimal("0"),
        binance_book_imbalance=Decimal("0"),
        bybit_book_imbalance=Decimal("0"),
        market_data_quality=Decimal("1"),
    )


def _keys(alerts) -> set[str]:
    return {alert.key for alert in alerts}


def test_market_state_detects_leveraged_long_building() -> None:
    snapshot = replace(
        _snapshot(),
        binance_oi_change_5m_pct=Decimal("0.40"),
        bybit_oi_change_5m_pct=Decimal("0.30"),
        trend_score_5m=Decimal("0.30"),
        futures_cvd_ratio_5m=Decimal("0.25"),
    )

    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    assert "leveraged_longs_building" in _keys(alerts)


def test_market_state_detects_short_covering() -> None:
    snapshot = replace(
        _snapshot(),
        binance_oi_change_5m_pct=Decimal("-0.50"),
        bybit_oi_change_5m_pct=Decimal("-0.30"),
        trend_score_5m=Decimal("0.25"),
    )

    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    assert "short_covering" in _keys(alerts)


def test_market_state_detects_spot_futures_leadership() -> None:
    snapshot = replace(
        _snapshot(),
        spot_cvd_ratio_5m=Decimal("0.35"),
        futures_cvd_ratio_5m=Decimal("-0.05"),
    )

    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    assert "spot_leads_buying" in _keys(alerts)


def test_market_state_detects_liquidation_wave() -> None:
    snapshot = replace(
        _snapshot(),
        long_liquidations_5m_usd=Decimal("25000"),
        short_liquidations_5m_usd=Decimal("475000"),
        liquidation_imbalance_5m=Decimal("0.90"),
    )

    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    assert "short_liquidation_wave" in _keys(alerts)


def test_market_state_detects_cross_exchange_book_pressure() -> None:
    snapshot = replace(
        _snapshot(),
        binance_book_imbalance=Decimal("0.45"),
        bybit_book_imbalance=Decimal("0.38"),
    )

    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    assert "cross_exchange_bid_pressure" in _keys(alerts)


def test_market_state_cooldown_suppresses_duplicate_alert() -> None:
    monitor = MarketStateAlertMonitor(cooldown=timedelta(minutes=5))
    snapshot = replace(
        _snapshot(),
        spot_cvd_ratio_5m=Decimal("0.40"),
        futures_cvd_ratio_5m=Decimal("0"),
    )

    first = monitor.evaluate(snapshot)
    second = monitor.evaluate(
        replace(
            snapshot,
            timestamp=snapshot.timestamp + timedelta(minutes=1),
        )
    )
    third = monitor.evaluate(
        replace(
            snapshot,
            timestamp=snapshot.timestamp + timedelta(minutes=6),
        )
    )

    assert "spot_leads_buying" in _keys(first)
    assert "spot_leads_buying" not in _keys(second)
    assert "spot_leads_buying" in _keys(third)


def test_market_state_refuses_low_quality_or_cold_start() -> None:
    monitor = MarketStateAlertMonitor()
    eventful = replace(
        _snapshot(),
        spot_cvd_ratio_5m=Decimal("0.40"),
        futures_cvd_ratio_5m=Decimal("0"),
    )

    assert monitor.evaluate(
        replace(eventful, market_data_quality=Decimal("0.50"))
    ) == ()
    assert monitor.evaluate(
        replace(eventful, history_seconds=30)
    ) == ()
