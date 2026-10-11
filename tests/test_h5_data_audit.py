from datetime import UTC, datetime

from crypto_signal_engine.examples.h5_data_audit import (
    EXPECTED_8H_FUNDING_POINTS,
    EXPECTED_HOURLY_POINTS,
    _duplicates,
)


def test_h5_expected_development_counts() -> None:
    assert EXPECTED_HOURLY_POINTS == 35064
    assert EXPECTED_8H_FUNDING_POINTS == 4383


def test_h5_audit_counts_duplicate_timestamps() -> None:
    first = datetime(2020, 1, 1, tzinfo=UTC)
    second = datetime(2020, 1, 2, tzinfo=UTC)

    assert _duplicates([first, first, first, second]) == 2
