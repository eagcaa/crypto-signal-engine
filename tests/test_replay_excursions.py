from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    build_excursion_stats,
    build_regime_excursion_stats,
    build_score_excursion_stats,
)


def _prediction(
    *,
    direction: PredictionDirection,
    horizon_seconds: int = 300,
) -> Prediction:
    created_at = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)
    return Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=horizon_seconds),
        horizon_seconds=horizon_seconds,
        direction=direction,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.83"),
        model_name="composite_rules_v4_5m",
    )


def test_excursion_stats_measure_full_horizon_mfe_and_mae() -> None:
    prediction = _prediction(direction=PredictionDirection.LONG)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("100.20"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("99.70"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=40),
            price=Decimal("100.50"),
        ),
    ]

    rows = build_excursion_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.median_mfe_pct == Decimal("0.500")
    assert row.median_mae_pct == Decimal("0.300")
    assert row.cost_clear_rate_pct == Decimal("100")


def test_excursion_stats_handle_short_direction() -> None:
    prediction = _prediction(direction=PredictionDirection.SHORT)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("99.60"),
        ),
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=20),
            price=Decimal("100.25"),
        ),
    ]

    row = build_excursion_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )[0]

    assert row.median_mfe_pct == Decimal("0.400")
    assert row.median_mae_pct == Decimal("0.250")



def test_score_excursion_stats_split_score_bins() -> None:
    first = _prediction(direction=PredictionDirection.LONG)
    second = Prediction(
        id=uuid4(),
        symbol=first.symbol,
        created_at=first.created_at,
        expires_at=first.expires_at,
        horizon_seconds=first.horizon_seconds,
        direction=first.direction,
        entry_price=first.entry_price,
        raw_score=Decimal("0.35"),
        data_quality=first.data_quality,
        model_name=first.model_name,
    )
    result = ReplayResult(
        decisions=(),
        predictions=(first, second),
        evaluations=(),
        open_predictions=(),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=first.created_at + timedelta(seconds=10),
            price=Decimal("100.20"),
        ),
    ]

    rows = build_score_excursion_stats(
        result,
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )

    assert len(rows) == 2
    assert rows[0].lower_bound == Decimal("0.30") or rows[1].lower_bound == Decimal("0.30")


def test_regime_excursion_stats_use_matching_horizon_regime() -> None:
    prediction = _prediction(direction=PredictionDirection.LONG)
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    from crypto_signal_engine.features.research import ResearchFeatureSnapshot

    feature = ResearchFeatureSnapshot(
        symbol="BTCUSDT",
        timestamp=prediction.created_at,
        price=Decimal("100"),
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
        trend_score_5m=Decimal("0.5"),
        trend_score_15m=Decimal("-0.5"),
        atr_pct_5m=Decimal("0.4"),
        atr_pct_15m=Decimal("0.6"),
        trend_regime_5m="uptrend",
        trend_regime_15m="downtrend",
        volatility_regime_5m="normal",
        volatility_regime_15m="high",
        binance_oi_change_5m_pct=None,
        binance_oi_change_15m_pct=None,
        bybit_oi_change_5m_pct=None,
        bybit_oi_change_15m_pct=None,
        binance_funding_rate=None,
        bybit_funding_rate=None,
        binance_long_short_ratio=None,
        bybit_long_short_ratio=None,
        binance_top_trader_long_short_ratio=None,
        binance_taker_buy_sell_ratio=None,
        long_liquidations_5m_usd=Decimal("0"),
        short_liquidations_5m_usd=Decimal("0"),
        liquidation_imbalance_5m=Decimal("0"),
        long_liquidations_15m_usd=Decimal("0"),
        short_liquidations_15m_usd=Decimal("0"),
        liquidation_imbalance_15m=Decimal("0"),
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
        market_data_quality=Decimal("0.83"),
    )
    points = [
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=prediction.created_at + timedelta(seconds=10),
            price=Decimal("100.20"),
        )
    ]

    row = build_regime_excursion_stats(
        result,
        [feature],
        points,
        round_trip_cost_pct=Decimal("0.12"),
    )[0]

    assert row.trend_regime == "uptrend"
    assert row.volatility_regime == "normal"
