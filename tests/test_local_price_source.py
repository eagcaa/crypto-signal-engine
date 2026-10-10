import zipfile
from datetime import UTC, datetime
from pathlib import Path

from crypto_signal_engine.replay.local_price_source import (
    LocalBinanceSpotAggTradePriceSource,
)


def _write_zip(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.csv", "\n".join(rows))


def test_local_price_source_reads_only_prediction_windows(tmp_path: Path) -> None:
    symbol = "BTCUSDT"
    path = (
        tmp_path
        / symbol
        / "spot"
        / "aggTrades"
        / f"{symbol}-aggTrades-2026-07-31.zip"
    )

    def micros(value: datetime) -> int:
        return int(value.timestamp() * 1_000_000)

    _write_zip(
        path,
        [
            "aggregate_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker",
            f"1,100,1,1,1,{micros(datetime(2026,7,31,23,50,tzinfo=UTC))},false",
            f"2,101,1,2,2,{micros(datetime(2026,7,31,23,55,tzinfo=UTC))},false",
            f"3,102,1,3,3,{micros(datetime(2026,7,31,23,59,tzinfo=UTC))},false",
        ],
    )

    source = LocalBinanceSpotAggTradePriceSource(tmp_path)
    points = source.fetch_price_points_for_windows(
        symbol,
        windows=[
            (
                datetime(2026, 7, 31, 23, 54, tzinfo=UTC),
                datetime(2026, 7, 31, 23, 56, tzinfo=UTC),
            )
        ],
    )

    assert [point.price for point in points] == [101]


def test_local_price_source_fails_when_required_archive_is_missing(
    tmp_path: Path,
) -> None:
    source = LocalBinanceSpotAggTradePriceSource(tmp_path)

    try:
        source.fetch_price_points_for_windows(
            "BTCUSDT",
            windows=[
                (
                    datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
                    datetime(2026, 7, 1, 0, 15, tzinfo=UTC),
                )
            ],
        )
    except FileNotFoundError as exc:
        assert "BTCUSDT-aggTrades-2026-07-01.zip" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
