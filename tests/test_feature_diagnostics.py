import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from crypto_signal_engine.examples.feature_diagnostics import (
    add_forward_returns,
    diagnose,
    load_frame,
)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _synthetic_rows(
    *,
    predictive: bool,
    count: int = 3000,
) -> list[dict]:
    rng = np.random.default_rng(7)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    signal = rng.normal(size=count)
    price = 100_000.0
    rows = []

    for index in range(count):
        rows.append(
            {
                "timestamp": (
                    start + timedelta(minutes=index)
                ).isoformat(),
                "price": str(price),
                "market_data_quality": "1",
                "spot_cvd_ratio_5m": str(signal[index]),
            }
        )
        drift = (
            0.0005 * signal[index]
            if predictive
            else 0.0
        )
        price *= (
            1.0
            + drift
            + rng.normal(scale=0.0001)
        )

    return rows


def test_forward_returns_use_exact_timestamps_and_skip_gaps(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": start.isoformat(),
            "price": "100",
            "market_data_quality": "1",
        },
        {
            "timestamp": (
                start + timedelta(minutes=16)
            ).isoformat(),
            "price": "200",
            "market_data_quality": "1",
        },
    ]
    path = tmp_path / "rows.jsonl"
    _write_rows(path, rows)

    frame = add_forward_returns(
        load_frame(path, ()),
        (15,),
    )

    assert np.isnan(frame["fwd_15m"].iloc[0])


def test_predictive_feature_shows_positive_stable_spread(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.jsonl"
    _write_rows(
        path,
        _synthetic_rows(predictive=True),
    )

    frame = add_forward_returns(
        load_frame(
            path,
            ("spot_cvd_ratio_5m",),
        ),
        (1,),
    )
    [result] = diagnose(
        frame,
        features=("spot_cvd_ratio_5m",),
        horizons_minutes=(1,),
    )

    assert result.spearman > 0.5
    assert result.spread_pct > 0
    assert result.first_half_spread_pct > 0
    assert result.second_half_spread_pct > 0


def test_noise_feature_has_no_meaningful_rank_correlation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.jsonl"
    _write_rows(
        path,
        _synthetic_rows(predictive=False),
    )

    frame = add_forward_returns(
        load_frame(
            path,
            ("spot_cvd_ratio_5m",),
        ),
        (1,),
    )
    [result] = diagnose(
        frame,
        features=("spot_cvd_ratio_5m",),
        horizons_minutes=(1,),
    )

    assert abs(result.spearman) < 0.1
