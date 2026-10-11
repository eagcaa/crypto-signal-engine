import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from crypto_signal_engine.examples.oi_feature_diagnostics import (
    add_oi_features,
    load_oi_delay,
)
from crypto_signal_engine.features.historical_metrics import (
    ArchivedMetricPoint,
)


def test_oi_diagnostics_accepts_oi_only_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alignment.json"
    path.write_text(
        json.dumps(
            {
                "status": "PASS_OI_ONLY",
                "symbol": "BTCUSDT",
                "selected_shift_minutes": 6,
            }
        ),
        encoding="utf-8",
    )

    assert load_oi_delay(
        artifact_path=path,
        symbol="BTCUSDT",
    ) == 6


def test_oi_diagnostics_rejects_failed_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alignment.json"
    path.write_text(
        json.dumps(
            {
                "status": "FAIL",
                "symbol": "BTCUSDT",
                "selected_shift_minutes": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="blocked"):
        load_oi_delay(
            artifact_path=path,
            symbol="BTCUSDT",
        )


def test_add_oi_features_respects_observable_delay(
    monkeypatch,
) -> None:
    start = datetime(
        2026,
        7,
        1,
        0,
        0,
        tzinfo=UTC,
    )
    points = [
        ArchivedMetricPoint(
            timestamp=start - timedelta(minutes=15),
            open_interest=Decimal("100"),
            open_interest_value=Decimal("1000"),
            long_short_ratio=None,
            top_trader_long_short_ratio=None,
            taker_buy_sell_ratio=None,
        ),
        ArchivedMetricPoint(
            timestamp=start - timedelta(minutes=10),
            open_interest=Decimal("101"),
            open_interest_value=Decimal("1010"),
            long_short_ratio=None,
            top_trader_long_short_ratio=None,
            taker_buy_sell_ratio=None,
        ),
        ArchivedMetricPoint(
            timestamp=start - timedelta(minutes=5),
            open_interest=Decimal("102"),
            open_interest_value=Decimal("1020"),
            long_short_ratio=None,
            top_trader_long_short_ratio=None,
            taker_buy_sell_ratio=None,
        ),
        ArchivedMetricPoint(
            timestamp=start,
            open_interest=Decimal("103"),
            open_interest_value=Decimal("1030"),
            long_short_ratio=None,
            top_trader_long_short_ratio=None,
            taker_buy_sell_ratio=None,
        ),
    ]

    monkeypatch.setattr(
        "crypto_signal_engine.examples.oi_feature_diagnostics.load_metrics_range",
        lambda **kwargs: points,
    )

    index = pd.DatetimeIndex(
        [
            start + timedelta(minutes=5),
            start + timedelta(minutes=6),
        ]
    )
    frame = pd.DataFrame(
        {
            "price": [100.0, 100.0],
            "market_data_quality": [1.0, 1.0],
        },
        index=index,
    )

    result = add_oi_features(
        frame=frame,
        symbol="BTCUSDT",
        start_day=date(2026, 7, 1),
        end_day=date(2026, 7, 1),
        input_root=Path("."),
        observable_delay_minutes=6,
    )

    before = result.loc[
        start + timedelta(minutes=5),
        "binance_oi_change_5m_pct",
    ]
    visible = result.loc[
        start + timedelta(minutes=6),
        "binance_oi_change_5m_pct",
    ]

    assert before == pytest.approx(
        float(
            (Decimal("1020") - Decimal("1010"))
            / Decimal("1010")
            * Decimal("100")
        )
    )
    assert visible == pytest.approx(
        float(
            (Decimal("1030") - Decimal("1020"))
            / Decimal("1020")
            * Decimal("100")
        )
    )
    assert before != visible
