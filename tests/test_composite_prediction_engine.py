from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.predictions import (
    CompositePredictionEngine,
    HistoricalCompatiblePredictionEngine,
    PredictionDecisionDirection,
)


def _ratio(value: str) -> Decimal:
    number = Decimal(value)
    if number > 0:
        return Decimal("0.80")
    if number < 0:
        return Decimal("-0.80")
    return Decimal("0")


def make_features(
    *,
    binance_book: str,
    bybit_book: str,
    spot_cvd_1m: str,
    spot_cvd_5m: str,
    futures_cvd_1m: str,
    futures_cvd_5m: str,
    binance_oi_5m: str,
    bybit_oi_5m: str,
    funding_binance: str,
    funding_bybit: str,
    binance_long_short: str,
    bybit_long_short: str,
    top_trader: str,
    taker_ratio: str,
    liq_imbalance: str,
    spot_sources: int = 2,
    futures_sources: int = 2,
    data_quality: str = "0.83",
    history_seconds: int = 900,
) -> ResearchFeatureSnapshot:
    return ResearchFeatureSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 8, 11, 0, tzinfo=UTC),
        price=Decimal("82500"),
        spot_cvd_1m=Decimal(spot_cvd_1m),
        spot_cvd_5m=Decimal(spot_cvd_5m),
        spot_cvd_15m=Decimal(spot_cvd_5m),
        futures_cvd_1m=Decimal(futures_cvd_1m),
        futures_cvd_5m=Decimal(futures_cvd_5m),
        futures_cvd_15m=Decimal(futures_cvd_5m),
        spot_cvd_ratio_1m=_ratio(spot_cvd_1m),
        spot_cvd_ratio_5m=_ratio(spot_cvd_5m),
        spot_cvd_ratio_15m=_ratio(spot_cvd_5m),
        futures_cvd_ratio_1m=_ratio(futures_cvd_1m),
        futures_cvd_ratio_5m=_ratio(futures_cvd_5m),
        futures_cvd_ratio_15m=_ratio(futures_cvd_5m),
        spot_trade_sources=spot_sources,
        futures_trade_sources=futures_sources,
        history_seconds=history_seconds,
        trend_score_5m=Decimal("0"),
        trend_score_15m=Decimal("0"),
        trend_score_1h=None,
        trend_score_4h=None,
        atr_pct_5m=Decimal("0.5"),
        atr_pct_15m=Decimal("0.8"),
        atr_pct_1h=None,
        atr_pct_4h=None,
        trend_regime_5m="range",
        trend_regime_15m="range",
        trend_regime_1h=None,
        trend_regime_4h=None,
        volatility_regime_5m="normal",
        volatility_regime_15m="normal",
        volatility_regime_1h=None,
        volatility_regime_4h=None,
        binance_oi_change_5m_pct=Decimal(binance_oi_5m),
        binance_oi_change_15m_pct=Decimal(binance_oi_5m),
        bybit_oi_change_5m_pct=Decimal(bybit_oi_5m),
        bybit_oi_change_15m_pct=Decimal(bybit_oi_5m),
        binance_funding_rate=Decimal(funding_binance),
        bybit_funding_rate=Decimal(funding_bybit),
        binance_long_short_ratio=Decimal(binance_long_short),
        bybit_long_short_ratio=Decimal(bybit_long_short),
        binance_top_trader_long_short_ratio=Decimal(top_trader),
        binance_taker_buy_sell_ratio=Decimal(taker_ratio),
        long_liquidations_5m_usd=Decimal("10000"),
        short_liquidations_5m_usd=Decimal("30000"),
        liquidation_imbalance_5m=Decimal(liq_imbalance),
        long_liquidations_15m_usd=Decimal("10000"),
        short_liquidations_15m_usd=Decimal("30000"),
        liquidation_imbalance_15m=Decimal(liq_imbalance),
        binance_book_imbalance=Decimal(binance_book),
        bybit_book_imbalance=Decimal(bybit_book),
        market_data_quality=Decimal(data_quality),
    )


def test_v2_generates_long_when_multiple_features_align() -> None:
    features = make_features(
        binance_book="0.8",
        bybit_book="0.6",
        spot_cvd_1m="3",
        spot_cvd_5m="5",
        futures_cvd_1m="4",
        futures_cvd_5m="7",
        binance_oi_5m="0.4",
        bybit_oi_5m="0.3",
        funding_binance="-0.0001",
        funding_bybit="-0.0001",
        binance_long_short="0.9",
        bybit_long_short="0.9",
        top_trader="0.9",
        taker_ratio="1.5",
        liq_imbalance="0.5",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.direction == PredictionDecisionDirection.LONG
    assert decision.prediction is not None
    assert decision.prediction.model_name == "composite_rules_v4_5m"
    assert decision.raw_score > Decimal("0.20")
    assert "order_book" in decision.feature_contributions
    assert "open_interest" in decision.feature_contributions


def test_v2_returns_no_trade_when_signals_conflict() -> None:
    features = make_features(
        binance_book="0.8",
        bybit_book="0.6",
        spot_cvd_1m="2",
        spot_cvd_5m="3",
        futures_cvd_1m="-2",
        futures_cvd_5m="-4",
        binance_oi_5m="0.4",
        bybit_oi_5m="0.4",
        funding_binance="0.0002",
        funding_bybit="0.0002",
        binance_long_short="2.0",
        bybit_long_short="2.0",
        top_trader="2.0",
        taker_ratio="0.5",
        liq_imbalance="0",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert decision.prediction is None
    assert abs(decision.raw_score) < Decimal("0.20")


def test_v2_discounts_single_exchange_futures_flow() -> None:
    engine = CompositePredictionEngine()
    features = make_features(
        binance_book="0",
        bybit_book="0",
        spot_cvd_1m="0",
        spot_cvd_5m="0",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0",
        bybit_oi_5m="0",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1",
        liq_imbalance="0",
        futures_sources=1,
    )

    decision = engine.decide(features, horizon_seconds=300)

    assert decision.feature_contributions["futures_cvd"] < Decimal("0.15")


def test_v2_rejects_low_quality_data() -> None:
    features = make_features(
        binance_book="0.9",
        bybit_book="0.9",
        spot_cvd_1m="5",
        spot_cvd_5m="5",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0.5",
        bybit_oi_5m="0.5",
        funding_binance="-0.0002",
        funding_bybit="-0.0002",
        binance_long_short="0.8",
        bybit_long_short="0.8",
        top_trader="0.8",
        taker_ratio="1.5",
        liq_imbalance="0.5",
        data_quality="0.50",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert decision.prediction is None


def test_v2_uses_different_windows_for_5m_and_15m() -> None:
    base_features = make_features(
        binance_book="0",
        bybit_book="0",
        spot_cvd_1m="5",
        spot_cvd_5m="5",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0.3",
        bybit_oi_5m="0.3",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1",
        liq_imbalance="0",
    )

    changed_15m = replace(
        base_features,
        spot_cvd_15m=Decimal("-10"),
        futures_cvd_15m=Decimal("-10"),
        spot_cvd_ratio_15m=Decimal("-0.80"),
        futures_cvd_ratio_15m=Decimal("-0.80"),
        binance_oi_change_15m_pct=Decimal("0.3"),
        bybit_oi_change_15m_pct=Decimal("0.3"),
    )

    engine = CompositePredictionEngine()

    five_minute_before = engine.decide(
        base_features,
        horizon_seconds=300,
    )
    five_minute_after = engine.decide(
        changed_15m,
        horizon_seconds=300,
    )
    fifteen_minute_before = engine.decide(
        base_features,
        horizon_seconds=900,
    )
    fifteen_minute_after = engine.decide(
        changed_15m,
        horizon_seconds=900,
    )

    # 5m profile must ignore changes that exist only in 15m fields.
    assert five_minute_before.raw_score == five_minute_after.raw_score

    # 15m profile must react to its 15m CVD/OI inputs.
    assert fifteen_minute_before.raw_score != fifteen_minute_after.raw_score
    assert fifteen_minute_before.prediction is not None
    assert fifteen_minute_before.prediction.model_name == "composite_rules_v4_15m"


def test_v2_rejects_unsupported_horizon() -> None:
    features = make_features(
        binance_book="0.8",
        bybit_book="0.8",
        spot_cvd_1m="5",
        spot_cvd_5m="5",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0.5",
        bybit_oi_5m="0.5",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1.5",
        liq_imbalance="0.5",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=60,
    )

    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert decision.prediction is None
    assert "Unsupported horizon" in decision.reason


def test_v2_waits_for_5m_warmup() -> None:
    features = make_features(
        binance_book="0.9",
        bybit_book="0.9",
        spot_cvd_1m="5",
        spot_cvd_5m="5",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0.5",
        bybit_oi_5m="0.5",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1.5",
        liq_imbalance="0.5",
        history_seconds=299,
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert decision.prediction is None
    assert "Warmup: 299s/300s" in decision.reason


def test_v2_waits_for_15m_warmup() -> None:
    features = make_features(
        binance_book="0.9",
        bybit_book="0.9",
        spot_cvd_1m="5",
        spot_cvd_5m="5",
        futures_cvd_1m="5",
        futures_cvd_5m="5",
        binance_oi_5m="0.5",
        bybit_oi_5m="0.5",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1.5",
        liq_imbalance="0.5",
        history_seconds=899,
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert decision.prediction is None
    assert "Warmup: 899s/900s" in decision.reason


def test_v2_distinguishes_weak_and_strong_flow_ratios() -> None:
    weak = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="0",
            futures_cvd_5m="0",
            binance_oi_5m="0",
            bybit_oi_5m="0",
            funding_binance="0",
            funding_bybit="0",
            binance_long_short="1",
            bybit_long_short="1",
            top_trader="1",
            taker_ratio="1",
            liq_imbalance="0",
        ),
        spot_cvd_ratio_1m=Decimal("0.05"),
        spot_cvd_ratio_5m=Decimal("0.05"),
    )
    strong = replace(
        weak,
        spot_cvd_ratio_1m=Decimal("0.80"),
        spot_cvd_ratio_5m=Decimal("0.80"),
    )

    engine = CompositePredictionEngine()
    weak_decision = engine.decide(weak, horizon_seconds=300)
    strong_decision = engine.decide(strong, horizon_seconds=300)

    assert (
        strong_decision.feature_contributions["spot_cvd"]
        > weak_decision.feature_contributions["spot_cvd"]
    )


def test_v3_uses_matching_horizon_trend() -> None:
    base = make_features(
        binance_book="0",
        bybit_book="0",
        spot_cvd_1m="0",
        spot_cvd_5m="0",
        futures_cvd_1m="0",
        futures_cvd_5m="0",
        binance_oi_5m="0",
        bybit_oi_5m="0",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1",
        liq_imbalance="0",
    )

    changed = replace(
        base,
        trend_score_5m=Decimal("0.8"),
        trend_score_15m=Decimal("-0.8"),
        trend_regime_5m="uptrend",
        trend_regime_15m="downtrend",
    )

    engine = CompositePredictionEngine()
    five = engine.decide(changed, horizon_seconds=300)
    fifteen = engine.decide(changed, horizon_seconds=900)

    assert five.feature_contributions["trend"] > 0
    assert fifteen.feature_contributions["trend"] < 0


def test_v3_treats_open_interest_contraction_as_neutral() -> None:
    features = make_features(
        binance_book="0",
        bybit_book="0",
        spot_cvd_1m="0",
        spot_cvd_5m="0",
        futures_cvd_1m="-5",
        futures_cvd_5m="-5",
        binance_oi_5m="-0.6",
        bybit_oi_5m="-0.4",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1",
        liq_imbalance="0",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.feature_contributions["open_interest"] == Decimal("0")


def test_v3_open_interest_expansion_confirms_futures_direction() -> None:
    features = make_features(
        binance_book="0",
        bybit_book="0",
        spot_cvd_1m="0",
        spot_cvd_5m="0",
        futures_cvd_1m="-5",
        futures_cvd_5m="-5",
        binance_oi_5m="0.6",
        bybit_oi_5m="0.4",
        funding_binance="0",
        funding_bybit="0",
        binance_long_short="1",
        bybit_long_short="1",
        top_trader="1",
        taker_ratio="1",
        liq_imbalance="0",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.feature_contributions["open_interest"] < Decimal("0")


def test_v4_uses_atr_aware_barriers() -> None:
    features = make_features(
        binance_book="0.8",
        bybit_book="0.6",
        spot_cvd_1m="3",
        spot_cvd_5m="5",
        futures_cvd_1m="4",
        futures_cvd_5m="7",
        binance_oi_5m="0.4",
        bybit_oi_5m="0.3",
        funding_binance="-0.0001",
        funding_bybit="-0.0001",
        binance_long_short="0.9",
        bybit_long_short="0.9",
        top_trader="0.9",
        taker_ratio="1.5",
        liq_imbalance="0.5",
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.prediction is not None
    assert decision.prediction.take_profit_pct == Decimal("0.45")
    assert decision.prediction.stop_loss_pct == Decimal("0.28")
    assert decision.prediction.model_name == "composite_rules_v4_5m"


def test_v4_rejects_low_volatility_target_that_cannot_clear_cost_floor() -> None:
    features = replace(
        make_features(
            binance_book="0.8",
            bybit_book="0.6",
            spot_cvd_1m="3",
            spot_cvd_5m="5",
            futures_cvd_1m="4",
            futures_cvd_5m="7",
            binance_oi_5m="0.4",
            bybit_oi_5m="0.3",
            funding_binance="-0.0001",
            funding_bybit="-0.0001",
            binance_long_short="0.9",
            bybit_long_short="0.9",
            top_trader="0.9",
            taker_ratio="1.5",
            liq_imbalance="0.5",
        ),
        atr_pct_5m=Decimal("0.10"),
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=300,
    )

    assert decision.prediction is None
    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert "cost+edge floor" in decision.reason



def test_v4_supports_one_hour_horizon_with_one_hour_atr_and_trend() -> None:
    features = replace(
        make_features(
            binance_book="0.8",
            bybit_book="0.6",
            spot_cvd_1m="3",
            spot_cvd_5m="5",
            futures_cvd_1m="4",
            futures_cvd_5m="7",
            binance_oi_5m="0.4",
            bybit_oi_5m="0.3",
            funding_binance="-0.0001",
            funding_bybit="-0.0001",
            binance_long_short="0.9",
            bybit_long_short="0.9",
            top_trader="0.9",
            taker_ratio="1.5",
            liq_imbalance="0.5",
            history_seconds=3600,
        ),
        trend_score_1h=Decimal("0.60"),
        trend_regime_1h="uptrend",
        volatility_regime_1h="normal",
        atr_pct_1h=Decimal("0.80"),
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=3600,
    )

    assert decision.prediction is not None
    assert decision.prediction.model_name == "shadow_composite_rules_v4_1h"
    assert CompositePredictionEngine.is_shadow_horizon(3600) is True
    assert all(
        horizon != 3600
        for horizon, _ in CompositePredictionEngine.current_model_names()
    )
    assert decision.prediction.take_profit_pct == Decimal("0.8800")
    assert decision.prediction.stop_loss_pct == Decimal("0.5600")
    assert decision.feature_contributions["trend"] > 0


def test_v4_waits_for_one_hour_warmup() -> None:
    features = replace(
        make_features(
            binance_book="0.8",
            bybit_book="0.6",
            spot_cvd_1m="3",
            spot_cvd_5m="5",
            futures_cvd_1m="4",
            futures_cvd_5m="7",
            binance_oi_5m="0.4",
            bybit_oi_5m="0.3",
            funding_binance="-0.0001",
            funding_bybit="-0.0001",
            binance_long_short="0.9",
            bybit_long_short="0.9",
            top_trader="0.9",
            taker_ratio="1.5",
            liq_imbalance="0.5",
            history_seconds=3599,
        ),
        trend_score_1h=Decimal("0.60"),
        atr_pct_1h=Decimal("0.80"),
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=3600,
    )

    assert decision.prediction is None
    assert "Warmup: 3599s/3600s" in decision.reason


def test_missing_liquidation_data_is_not_treated_as_neutral_zero() -> None:
    features = replace(
        make_features(
            binance_book="0.8",
            bybit_book="0.6",
            spot_cvd_1m="3",
            spot_cvd_5m="5",
            futures_cvd_1m="4",
            futures_cvd_5m="7",
            binance_oi_5m="0.4",
            bybit_oi_5m="0.3",
            funding_binance="-0.0001",
            funding_bybit="-0.0001",
            binance_long_short="0.9",
            bybit_long_short="0.9",
            top_trader="0.9",
            taker_ratio="1.5",
            liq_imbalance="0.5",
        ),
        liquidation_data_available=False,
    )

    decision = CompositePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert "liquidations" not in decision.feature_contributions


def test_historical_compatible_profile_excludes_order_book_and_liquidations() -> None:
    features = make_features(
        binance_book="0.8",
        bybit_book="0.6",
        spot_cvd_1m="3",
        spot_cvd_5m="5",
        futures_cvd_1m="4",
        futures_cvd_5m="7",
        binance_oi_5m="0.4",
        bybit_oi_5m="0.3",
        funding_binance="-0.0001",
        funding_bybit="-0.0001",
        binance_long_short="0.9",
        bybit_long_short="0.9",
        top_trader="0.9",
        taker_ratio="1.5",
        liq_imbalance="0.5",
        data_quality="1.0",
    )

    decision = HistoricalCompatiblePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert "order_book" not in decision.feature_contributions
    assert "liquidations" not in decision.feature_contributions
    assert decision.prediction is not None
    assert decision.prediction.model_name == "historical_compatible_v1_15m"


def test_historical_profile_requires_full_binance_stream_quality() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="5",
            futures_cvd_5m="5",
            binance_oi_5m="0.4",
            bybit_oi_5m="0",
            funding_binance="-0.0001",
            funding_bybit="0",
            binance_long_short="0.9",
            bybit_long_short="1",
            top_trader="0.9",
            taker_ratio="1.5",
            liq_imbalance="0",
            spot_sources=1,
            futures_sources=1,
            data_quality="0.50",
        ),
        dataset_provenance="binance_vision_historical_compatible_v1",
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
    )

    decision = HistoricalCompatiblePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert decision.prediction is None
    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert "data quality" in decision.reason.lower()


def test_historical_profile_does_not_half_single_source_cvd() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="5",
            futures_cvd_5m="5",
            binance_oi_5m="0",
            bybit_oi_5m="0",
            funding_binance="0",
            funding_bybit="0",
            binance_long_short="1",
            bybit_long_short="1",
            top_trader="1",
            taker_ratio="1",
            liq_imbalance="0",
            spot_sources=1,
            futures_sources=1,
            data_quality="1.0",
        ),
        spot_cvd_ratio_15m=Decimal("0.80"),
        futures_cvd_ratio_15m=Decimal("0.80"),
        dataset_provenance="binance_vision_historical_compatible_v1",
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
    )

    live = CompositePredictionEngine(
        minimum_data_quality=Decimal("0"),
    ).decide(features, horizon_seconds=900)
    historical = HistoricalCompatiblePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert historical.feature_contributions["spot_cvd"] == Decimal("0.1440")
    assert historical.feature_contributions["futures_cvd"] == Decimal("0.1440")
    assert historical.feature_contributions["spot_cvd"] == (
        live.feature_contributions["spot_cvd"] * Decimal("2")
    )
    assert historical.feature_contributions["futures_cvd"] == (
        live.feature_contributions["futures_cvd"] * Decimal("2")
    )


def test_historical_profile_normalizes_score_by_active_weight() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="5",
            futures_cvd_5m="5",
            binance_oi_5m="0",
            bybit_oi_5m="0",
            funding_binance="0",
            funding_bybit="0",
            binance_long_short="1",
            bybit_long_short="1",
            top_trader="1",
            taker_ratio="1",
            liq_imbalance="0",
            spot_sources=1,
            futures_sources=1,
            data_quality="1.0",
        ),
        spot_cvd_ratio_15m=Decimal("0.80"),
        futures_cvd_ratio_15m=Decimal("0.80"),
        dataset_provenance="binance_vision_historical_compatible_v1",
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
    )

    engine = HistoricalCompatiblePredictionEngine(
        minimum_abs_score=Decimal("0"),
    )
    decision = engine.decide(features, horizon_seconds=900)

    contribution_sum = sum(
        decision.feature_contributions.values(),
        Decimal("0"),
    )
    expected_active_weight = Decimal("0.85")
    assert decision.raw_score == contribution_sum / expected_active_weight


def test_historical_profile_rejects_low_active_weight_before_normalization() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="0",
            spot_cvd_5m="0",
            futures_cvd_1m="0",
            futures_cvd_5m="0",
            binance_oi_5m="0",
            bybit_oi_5m="0",
            funding_binance="0",
            funding_bybit="0",
            binance_long_short="1",
            bybit_long_short="1",
            top_trader="1",
            taker_ratio="1",
            liq_imbalance="0",
            spot_sources=0,
            futures_sources=0,
            data_quality="1.0",
        ),
        trend_score_15m=Decimal("0.40"),
        binance_oi_change_15m_pct=None,
        bybit_oi_change_15m_pct=None,
        binance_funding_rate=None,
        bybit_funding_rate=None,
        binance_long_short_ratio=None,
        bybit_long_short_ratio=None,
        binance_top_trader_long_short_ratio=None,
        binance_taker_buy_sell_ratio=None,
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
        dataset_provenance="binance_vision_historical_compatible_v1",
    )

    decision = HistoricalCompatiblePredictionEngine().decide(
        features,
        horizon_seconds=900,
    )

    assert decision.prediction is None
    assert decision.direction == PredictionDecisionDirection.NO_TRADE
    assert "Insufficient active feature weight: 0.15/0.50" in decision.reason


def test_historical_profile_accepts_active_weight_at_minimum_boundary() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="5",
            futures_cvd_5m="5",
            binance_oi_5m="0",
            bybit_oi_5m="0",
            funding_binance="0",
            funding_bybit="0",
            binance_long_short="1",
            bybit_long_short="1",
            top_trader="1",
            taker_ratio="1",
            liq_imbalance="0",
            spot_sources=1,
            futures_sources=1,
            data_quality="1.0",
        ),
        binance_oi_change_15m_pct=None,
        bybit_oi_change_15m_pct=None,
        binance_funding_rate=None,
        bybit_funding_rate=None,
        binance_long_short_ratio=None,
        bybit_long_short_ratio=None,
        binance_top_trader_long_short_ratio=None,
        binance_taker_buy_sell_ratio=None,
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
        dataset_provenance="binance_vision_historical_compatible_v1",
    )

    decision = HistoricalCompatiblePredictionEngine(
        minimum_abs_score=Decimal("0"),
    ).decide(features, horizon_seconds=900)

    # 15m spot CVD (0.18) + futures CVD (0.18) + trend (0.15) = 0.51.
    assert set(decision.feature_contributions) == {
        "spot_cvd",
        "futures_cvd",
        "trend",
    }
    assert decision.prediction is not None


def test_historical_v1_ignores_metrics_even_when_present() -> None:
    features = replace(
        make_features(
            binance_book="0",
            bybit_book="0",
            spot_cvd_1m="5",
            spot_cvd_5m="5",
            futures_cvd_1m="5",
            futures_cvd_5m="5",
            binance_oi_5m="0.8",
            bybit_oi_5m="0.8",
            funding_binance="-0.0005",
            funding_bybit="-0.0005",
            binance_long_short="0.6",
            bybit_long_short="0.6",
            top_trader="0.6",
            taker_ratio="2.0",
            liq_imbalance="0",
            spot_sources=1,
            futures_sources=1,
            data_quality="1.0",
        ),
        dataset_provenance="binance_vision_historical_compatible_v1",
        liquidation_data_available=False,
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
    )

    decision = HistoricalCompatiblePredictionEngine(
        minimum_abs_score=Decimal("0"),
    ).decide(features, horizon_seconds=900)

    assert set(decision.feature_contributions) == {
        "spot_cvd",
        "futures_cvd",
        "trend",
    }
