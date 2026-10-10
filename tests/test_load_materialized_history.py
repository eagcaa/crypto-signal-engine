import json
from datetime import UTC, date, datetime
from pathlib import Path

from crypto_signal_engine.examples.load_materialized_history import (
    default_materialized_path,
    snapshot_from_json,
)


def test_default_materialized_path_matches_materializer_naming(tmp_path: Path) -> None:
    path = default_materialized_path(
        symbol="btcusdt",
        days=7,
        end_day=date(2026, 10, 9),
        input_root=tmp_path,
    )

    assert path == (
        tmp_path
        / "BTCUSDT"
        / "materialized"
        / "features-2026-10-03-2026-10-09.jsonl"
    )


def test_snapshot_from_json_preserves_provenance_and_decimal_fields() -> None:
    payload = {
        "symbol": "BTCUSDT",
        "timestamp": datetime(2026, 10, 9, 12, 0, tzinfo=UTC).isoformat(),
        "price": "100.5",
        "spot_cvd_1m": "1",
        "spot_cvd_5m": "2",
        "spot_cvd_15m": "3",
        "futures_cvd_1m": "4",
        "futures_cvd_5m": "5",
        "futures_cvd_15m": "6",
        "spot_cvd_ratio_1m": "0.1",
        "spot_cvd_ratio_5m": "0.2",
        "spot_cvd_ratio_15m": "0.3",
        "futures_cvd_ratio_1m": "0.4",
        "futures_cvd_ratio_5m": "0.5",
        "futures_cvd_ratio_15m": "0.6",
        "spot_trade_sources": 1,
        "futures_trade_sources": 1,
        "history_seconds": 900,
        "trend_score_5m": None,
        "trend_score_15m": "0.1",
        "trend_score_1h": None,
        "trend_score_4h": None,
        "atr_pct_5m": None,
        "atr_pct_15m": "0.2",
        "atr_pct_1h": None,
        "atr_pct_4h": None,
        "trend_regime_5m": None,
        "trend_regime_15m": "range",
        "trend_regime_1h": None,
        "trend_regime_4h": None,
        "volatility_regime_5m": None,
        "volatility_regime_15m": "high",
        "volatility_regime_1h": None,
        "volatility_regime_4h": None,
        "binance_oi_change_5m_pct": None,
        "binance_oi_change_15m_pct": None,
        "bybit_oi_change_5m_pct": None,
        "bybit_oi_change_15m_pct": None,
        "binance_funding_rate": None,
        "bybit_funding_rate": None,
        "binance_long_short_ratio": None,
        "bybit_long_short_ratio": None,
        "binance_top_trader_long_short_ratio": None,
        "binance_taker_buy_sell_ratio": None,
        "long_liquidations_5m_usd": "0",
        "short_liquidations_5m_usd": "0",
        "liquidation_imbalance_5m": "0",
        "long_liquidations_15m_usd": "0",
        "short_liquidations_15m_usd": "0",
        "liquidation_imbalance_15m": "0",
        "binance_book_imbalance": None,
        "bybit_book_imbalance": None,
        "market_data_quality": "1",
        "liquidation_data_available": False,
        "dataset_provenance": "binance_vision_historical_compatible_v1",
    }

    snapshot = snapshot_from_json(json.loads(json.dumps(payload)))

    assert snapshot.price is not None
    assert str(snapshot.price) == "100.5"
    assert snapshot.market_data_quality == 1
    assert snapshot.dataset_provenance == (
        "binance_vision_historical_compatible_v1"
    )


def test_default_materialized_path_supports_v2_prefix(tmp_path: Path) -> None:
    path = default_materialized_path(
        symbol="btcusdt",
        days=62,
        end_day=date(2026, 8, 31),
        input_root=tmp_path,
        historical_version="v2",
    )

    assert path == (
        tmp_path
        / "BTCUSDT"
        / "materialized"
        / "features-v2-2026-07-01-2026-08-31.jsonl"
    )
