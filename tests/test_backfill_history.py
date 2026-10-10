from datetime import date

from crypto_signal_engine.examples.backfill_history import (
    daily_archive_specs,
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
