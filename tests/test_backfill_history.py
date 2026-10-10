from datetime import date

from crypto_signal_engine.examples.backfill_history import (
    TECHNICAL_WARMUP_DAYS,
    archive_timestamp_to_datetime,
    daily_archive_specs,
    kline_archive_specs,
    requested_days,
)


def test_backfill_uses_expected_binance_vision_daily_paths() -> None:
    specs = daily_archive_specs(
        symbol="btcusdt",
        day=date(2026, 10, 9),
    )
    by_name = {item.dataset: item for item in specs}

    assert len(specs) == 7
    assert by_name["spot_aggTrades"].url.endswith(
        "/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-10-09.zip"
    )
    assert by_name["futures_um_aggTrades"].url.endswith(
        "/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-10-09.zip"
    )
    assert by_name["futures_um_metrics"].url.endswith(
        "/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-10-09.zip"
    )
    assert by_name["spot_klines_1h"].url.endswith(
        "/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10-09.zip"
    )


def test_requested_days_is_inclusive_and_ordered() -> None:
    assert requested_days(
        days=3,
        end_day=date(2026, 10, 9),
    ) == (
        date(2026, 10, 7),
        date(2026, 10, 8),
        date(2026, 10, 9),
    )


def test_archive_timestamp_supports_milliseconds_and_microseconds() -> None:
    millisecond_value = 1760000000000
    microsecond_value = 1760000000000000

    assert archive_timestamp_to_datetime(
        millisecond_value
    ) == archive_timestamp_to_datetime(microsecond_value)


def test_archive_timestamp_rejects_impossible_values() -> None:
    try:
        archive_timestamp_to_datetime(123)
    except ValueError as exc:
        assert "supported range" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_backfill_downloads_eighteen_days_of_technical_warmup() -> None:
    assert TECHNICAL_WARMUP_DAYS == 18
    specs = kline_archive_specs(
        symbol="BTCUSDT",
        day=date(2026, 9, 21),
    )
    assert len(specs) == 4
    assert {item.dataset for item in specs} == {
        "spot_klines_5m",
        "spot_klines_15m",
        "spot_klines_1h",
        "spot_klines_4h",
    }
