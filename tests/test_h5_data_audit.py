from datetime import UTC, datetime

import pandas as pd

from crypto_signal_engine.examples.h5_data_audit import (
    EXPECTED_8H_FUNDING_POINTS,
    EXPECTED_HOURLY_POINTS,
    _duplicates,
    _max_contiguous_missing_hours,
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
