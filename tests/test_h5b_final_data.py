import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from crypto_signal_engine.examples.backfill_h5b_final import (
    Month,
    fred_dtb3_final_url,
    monthly_archive_specs,
    validate_final_range,
)
from crypto_signal_engine.examples.h5b_final_data_audit import (
    EXPECTED_8H_FUNDING_POINTS,
    MAX_MISSING_SPOT_HOURS,
    _spot_is_tolerable,
)
from crypto_signal_engine.examples.h5b_final_test import (
    EXPECTED_HOURLY_POINTS,
    load_final_kline_prices,
    require_final_audit_pass,
)


def test_h5b_downloader_accepts_only_frozen_final_range() -> None:
    validate_final_range(
        start_day=date(2024, 1, 1),
        end_day=date(2025, 12, 31),
    )

    with pytest.raises(ValueError, match="only the frozen final-test range"):
        validate_final_range(
            start_day=date(2024, 1, 2),
            end_day=date(2025, 12, 31),
        )


def test_h5b_final_archive_paths_are_frozen_sources() -> None:
    specs = monthly_archive_specs(
        symbol="btcusdt",
        month=Month(2025, 1),
    )
    urls = {item.dataset: item.url for item in specs}

    assert urls["futures_um_fundingRate"].endswith(
        "/futures/um/monthly/fundingRate/BTCUSDT/"
        "BTCUSDT-fundingRate-2025-01.zip"
    )
    assert urls["spot_klines_1h"].endswith(
        "/spot/monthly/klines/BTCUSDT/1h/"
        "BTCUSDT-1h-2025-01.zip"
    )
    assert urls["futures_um_klines_1h"].endswith(
        "/futures/um/monthly/klines/BTCUSDT/1h/"
        "BTCUSDT-1h-2025-01.zip"
    )


def test_h5b_fred_url_is_bounded_to_final_test() -> None:
    url = fred_dtb3_final_url()

    assert "cosd=2023-12-01" in url
    assert "coed=2025-12-31" in url
    assert "2026" not in url


def test_h5b_final_expected_counts_and_spot_tolerance() -> None:
    assert EXPECTED_HOURLY_POINTS == 17_544
    assert EXPECTED_8H_FUNDING_POINTS == 2_193
    assert MAX_MISSING_SPOT_HOURS == 17

    tolerable = [
        datetime(2024, 1, day, 0, 59, 59, 999000, tzinfo=UTC)
        for day in range(1, 18)
    ]
    too_many = tolerable + [
        datetime(2024, 1, 18, 0, 59, 59, 999000, tzinfo=UTC)
    ]

    import pandas as pd

    assert _spot_is_tolerable(
        pd.DatetimeIndex(tolerable),
        unexpected_count=0,
        duplicates=0,
    )
    assert not _spot_is_tolerable(
        pd.DatetimeIndex(too_many),
        unexpected_count=0,
        duplicates=0,
    )


def _write_kline_zip(
    path: Path,
    *,
    open_timestamp: datetime,
    microseconds: bool,
) -> None:
    multiplier = 1_000_000 if microseconds else 1_000
    open_time = int(open_timestamp.timestamp() * multiplier)
    close_time = int(
        (
            open_timestamp.timestamp() + 3599.999
        )
        * multiplier
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            path.with_suffix(".csv").name,
            ",".join(
                [
                    str(open_time),
                    "100",
                    "101",
                    "99",
                    "100",
                    "10",
                    str(close_time),
                    "1000",
                    "10",
                    "5",
                    "500",
                    "0",
                ]
            )
            + "\n",
        )


def test_h5b_final_kline_loader_handles_ms_and_us(tmp_path: Path) -> None:
    ms_path = tmp_path / "BTCUSDT-1h-2024-01.zip"
    us_path = tmp_path / "BTCUSDT-1h-2025-01.zip"
    _write_kline_zip(
        ms_path,
        open_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        microseconds=False,
    )
    _write_kline_zip(
        us_path,
        open_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        microseconds=True,
    )

    points = load_final_kline_prices([ms_path, us_path])

    assert points[0].timestamp == datetime(
        2024,
        1,
        1,
        0,
        59,
        59,
        999000,
        tzinfo=UTC,
    )
    assert points[1].timestamp == datetime(
        2025,
        1,
        1,
        0,
        59,
        59,
        999000,
        tzinfo=UTC,
    )


def test_h5b_final_runner_requires_pass_audit(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="PASS data-audit artifact"):
        require_final_audit_pass(
            input_root=tmp_path,
            symbol="BTCUSDT",
        )

    target = tmp_path / "BTCUSDT" / "h5b-final-audit.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "status": "PASS",
                "symbol": "BTCUSDT",
                "start": "2024-01-01",
                "end": "2025-12-31",
            }
        ),
        encoding="utf-8",
    )

    require_final_audit_pass(
        input_root=tmp_path,
        symbol="BTCUSDT",
    )
