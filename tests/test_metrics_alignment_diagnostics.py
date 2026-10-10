import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.examples.metrics_alignment import (
    ArchiveRow,
    LiveRow,
    duplicate_create_times,
    oi_shift_results,
    parse_create_time,
    ratio_lag_results,
    read_metrics_archive,
    recommended_delay_minutes,
)


START = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)


def _oi_at(minute: float) -> Decimal:
    return (
        Decimal("90000")
        + Decimal(str(minute))
        * Decimal("10")
    )


def _live_oi_rows() -> list[LiveRow]:
    return [
        LiveRow(
            timestamp=(
                START
                + timedelta(seconds=30 * index)
            ),
            values={
                "open_interest": _oi_at(index / 2)
            },
        )
        for index in range(240)
    ]


def test_parse_create_time_accepts_text_and_epoch() -> None:
    assert parse_create_time(
        "2026-10-09 00:05:00"
    ) == START + timedelta(minutes=5)

    epoch_ms = str(
        int(
            (
                START
                + timedelta(minutes=5)
            ).timestamp()
            * 1000
        )
    )
    assert parse_create_time(
        epoch_ms
    ) == START + timedelta(minutes=5)


def test_oi_scan_finds_zero_shift_when_archive_matches_label_time() -> None:
    archive = [
        ArchiveRow(
            create_time=(
                START
                + timedelta(minutes=minute)
            ),
            values={
                "sum_open_interest": _oi_at(
                    minute
                )
            },
        )
        for minute in range(20, 100, 5)
    ]

    results = oi_shift_results(
        archive,
        _live_oi_rows(),
    )
    best = min(
        results,
        key=lambda item: (
            item.median_abs_rel_error
        ),
    )

    assert best.shift_minutes == 0


def test_oi_scan_detects_label_that_is_five_minutes_early() -> None:
    archive = [
        ArchiveRow(
            create_time=(
                START
                + timedelta(minutes=minute)
            ),
            values={
                "sum_open_interest": _oi_at(
                    minute + 5
                )
            },
        )
        for minute in range(20, 100, 5)
    ]

    results = oi_shift_results(
        archive,
        _live_oi_rows(),
    )
    best = min(
        results,
        key=lambda item: (
            item.median_abs_rel_error
        ),
    )

    assert best.shift_minutes == 5


def test_ratio_lag_measures_first_live_poll_showing_value() -> None:
    archive = [
        ArchiveRow(
            create_time=(
                START
                + timedelta(minutes=minute)
            ),
            values={
                "count_toptrader_long_short_ratio": (
                    Decimal("1.50")
                    + Decimal(minute) / 1000
                )
            },
        )
        for minute in range(20, 100, 5)
    ]

    live: list[LiveRow] = []
    for index in range(240):
        timestamp = (
            START
            + timedelta(seconds=30 * index)
        )
        visible_label_minute = int(
            (
                (
                    timestamp
                    - timedelta(seconds=90)
                    - START
                ).total_seconds()
                // 300
            )
            * 5
        )
        live.append(
            LiveRow(
                timestamp=timestamp,
                values={
                    "top_trader_long_short_ratio": (
                        Decimal("1.50")
                        + Decimal(
                            visible_label_minute
                        )
                        / 1000
                    )
                },
            )
        )

    results = ratio_lag_results(
        archive,
        live,
    )
    matched = next(
        item
        for item in results
        if (
            item.archive_column
            == "count_toptrader_long_short_ratio"
            and item.live_field
            == "top_trader_long_short_ratio"
        )
    )

    assert matched.match_rate == 1.0
    assert matched.median_lag_seconds == 90

    delay, _ = recommended_delay_minutes(
        [],
        results,
    )
    assert delay == 2


def test_recommendation_is_ambiguous_without_confident_ratio_matches() -> None:
    delay, notes = recommended_delay_minutes(
        [],
        [],
    )

    assert delay is None
    assert any(
        "AMBIGUOUS" in note
        for note in notes
    )


def test_reads_archive_and_reports_duplicates(
    tmp_path: Path,
) -> None:
    csv_text = (
        "create_time,symbol,sum_open_interest,"
        "sum_open_interest_value,"
        "count_toptrader_long_short_ratio,"
        "sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,"
        "sum_taker_long_short_vol_ratio\n"
        "2026-10-09 00:05:00,BTCUSDT,"
        "90050,1,1.5,1.6,1.4,0.9\n"
        "2026-10-09 00:05:00,BTCUSDT,"
        "90050,1,1.5,1.6,1.4,0.9\n"
        "2026-10-09 00:10:00,BTCUSDT,"
        "90100,1,1.5,1.6,1.4,0.9\n"
    )
    path = (
        tmp_path
        / "BTCUSDT-metrics-2026-10-09.zip"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "BTCUSDT-metrics-2026-10-09.csv",
            csv_text,
        )

    rows = read_metrics_archive(path)

    assert len(rows) == 3
    assert (
        rows[0].values["sum_open_interest"]
        == Decimal("90050")
    )
    assert duplicate_create_times(rows) == [
        START + timedelta(minutes=5)
    ]
