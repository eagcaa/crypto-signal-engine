from datetime import UTC, datetime

import pandas as pd

from crypto_signal_engine.examples.h5_data_audit import (
    EXPECTED_8H_FUNDING_POINTS,
    EXPECTED_HOURLY_POINTS,
    _duplicates,
    _max_contiguous_missing_hours,
    _missing_ranges,
    _spot_missing_is_tolerable,
)


def test_h5_expected_development_counts() -> None:
    assert EXPECTED_HOURLY_POINTS == 35064
    assert EXPECTED_8H_FUNDING_POINTS == 4383


def test_h5_audit_counts_duplicate_timestamps() -> None:
    first = datetime(2020, 1, 1, tzinfo=UTC)
    second = datetime(2020, 1, 2, tzinfo=UTC)

    assert _duplicates([first, first, first, second]) == 2


def test_h5_audit_reports_longest_contiguous_gap() -> None:
    missing = pd.DatetimeIndex(
        [
            "2020-01-01T00:59:59.999Z",
            "2020-01-01T01:59:59.999Z",
            "2020-01-01T04:59:59.999Z",
        ]
    )

    assert _max_contiguous_missing_hours(missing) == 2


def test_h5_audit_groups_missing_ranges() -> None:
    missing = pd.DatetimeIndex(
        [
            "2020-01-01T00:59:59.999Z",
            "2020-01-01T01:59:59.999Z",
            "2020-01-01T04:59:59.999Z",
            "2020-01-01T05:59:59.999Z",
            "2020-01-01T06:59:59.999Z",
        ]
    )

    ranges = _missing_ranges(missing)

    assert [(item[0].hour, item[1].hour, item[2]) for item in ranges] == [
        (0, 1, 2),
        (4, 6, 3),
    ]


def test_h5_spot_tolerance_accepts_35_short_missing_hours() -> None:
    missing = pd.date_range(
        "2020-01-01T00:59:59.999Z",
        periods=35,
        freq="24h",
    )

    assert _spot_missing_is_tolerable(
        missing,
        unexpected_count=0,
    )


def test_h5_spot_tolerance_rejects_36_missing_hours() -> None:
    missing = pd.date_range(
        "2020-01-01T00:59:59.999Z",
        periods=36,
        freq="24h",
    )

    assert not _spot_missing_is_tolerable(
        missing,
        unexpected_count=0,
    )


def test_h5_spot_tolerance_rejects_gap_longer_than_6_hours() -> None:
    missing = pd.date_range(
        "2020-01-01T00:59:59.999Z",
        periods=7,
        freq="h",
    )

    assert not _spot_missing_is_tolerable(
        missing,
        unexpected_count=0,
    )
