import csv
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.examples.backfill_metrics import (
    date_range,
    metrics_archive_spec,
)
from crypto_signal_engine.examples.validate_metrics_alignment import (
    ArchivedMetricPoint,
    LiveOiPoint,
    count_duplicate_timestamps,
    evaluate_alignment,
    load_metrics_archive,
)


def _archive_point(
    timestamp: datetime,
    open_interest: str,
) -> ArchivedMetricPoint:
    return ArchivedMetricPoint(
        timestamp=timestamp,
        open_interest=Decimal(open_interest),
        open_interest_value=None,
        long_short_ratio=Decimal("1.1"),
        top_trader_long_short_ratio=Decimal("1.2"),
        taker_buy_sell_ratio=Decimal("1.05"),
    )


def test_metrics_archive_spec_uses_binance_vision_daily_metrics_path() -> None:
    spec = metrics_archive_spec(
        symbol="btcusdt",
        day=datetime(2026, 10, 8, tzinfo=UTC).date(),
    )
    assert spec.dataset == "futures_um_metrics"
    assert spec.url.endswith(
        "/futures/um/daily/metrics/BTCUSDT/"
        "BTCUSDT-metrics-2026-10-08.zip"
    )


def test_metrics_date_range_is_inclusive() -> None:
    start = datetime(2026, 10, 8, tzinfo=UTC).date()
    end = datetime(2026, 10, 10, tzinfo=UTC).date()
    assert date_range(start, end) == (
        start,
        start + timedelta(days=1),
        end,
    )


def test_load_metrics_archive_reads_named_columns(tmp_path: Path) -> None:
    archive_path = tmp_path / "BTCUSDT-metrics-2026-10-08.zip"
    csv_path = "BTCUSDT-metrics-2026-10-08.csv"

    rows = [
        {
            "create_time": "2026-10-08 00:05:00",
            "symbol": "BTCUSDT",
            "sum_open_interest": "12345.5",
            "sum_open_interest_value": "987654321",
            "count_toptrader_long_short_ratio": "1.20",
            "count_long_short_ratio": "1.10",
            "sum_taker_long_short_vol_ratio": "1.05",
        }
    ]

    with zipfile.ZipFile(archive_path, "w") as archive:
        from io import StringIO

        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        archive.writestr(csv_path, buffer.getvalue())

    points = load_metrics_archive(archive_path)

    assert len(points) == 1
    assert points[0].timestamp == datetime(
        2026, 10, 8, 0, 5, tzinfo=UTC
    )
    assert points[0].open_interest == Decimal("12345.5")
    assert points[0].long_short_ratio == Decimal("1.10")
    assert points[0].top_trader_long_short_ratio == Decimal("1.20")
    assert points[0].taker_buy_sell_ratio == Decimal("1.05")


def test_duplicate_metric_timestamps_are_detected() -> None:
    timestamp = datetime(2026, 10, 8, tzinfo=UTC)
    points = [
        _archive_point(timestamp, "100"),
        _archive_point(timestamp, "101"),
    ]
    assert count_duplicate_timestamps(points) == 1


def test_alignment_selects_plus_five_minute_observable_shift() -> None:
    start = datetime(2026, 10, 8, tzinfo=UTC)
    archive_points = [
        _archive_point(
            start + timedelta(minutes=5 * index),
            str(1000 + 100 * index),
        )
        for index in range(12)
    ]

    live_points = [
        LiveOiPoint(
            timestamp=point.timestamp + timedelta(minutes=5),
            open_interest=point.open_interest,
        )
        for point in archive_points
    ]

    result = evaluate_alignment(archive_points, live_points)

    assert result.status == "PASS"
    assert result.selected_shift_minutes == 5


def test_alignment_is_ambiguous_when_all_shifts_fit_equally() -> None:
    start = datetime(2026, 10, 8, tzinfo=UTC)
    archive_points = [
        _archive_point(
            start + timedelta(minutes=5 * index),
            "1000",
        )
        for index in range(12)
    ]
    live_points = [
        LiveOiPoint(
            timestamp=start + timedelta(minutes=index),
            open_interest=Decimal("1000"),
        )
        for index in range(70)
    ]

    result = evaluate_alignment(archive_points, live_points)

    assert result.status == "AMBIGUOUS"
    assert result.selected_shift_minutes is None
