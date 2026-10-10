import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.examples.materialize_history import (
    HistoricalTrade,
    RollingTradeWindow,
    build_historical_snapshot,
    iter_agg_trades,
    materialize,
)


def _write_zip(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.csv", "\n".join(rows))


def test_agg_trade_parser_handles_microseconds_and_taker_side(tmp_path: Path) -> None:
    path = tmp_path / "trades.zip"
    _write_zip(
        path,
        [
            "aggregate_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker",
            "1,100,2,1,1,1760000000000000,false",
            "2,101,3,2,2,1760000001000000,true",
        ],
    )

    rows = list(iter_agg_trades([path]))

    assert rows[0].timestamp == datetime.fromtimestamp(1760000000, tz=UTC)
    assert rows[0].signed_quantity == Decimal("2")
    assert rows[1].signed_quantity == Decimal("-3")


def test_historical_quality_uses_recent_trade_freshness() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    spot = RollingTradeWindow()
    futures = RollingTradeWindow()

    spot.add_many([
        HistoricalTrade(
            now.replace(second=30) - timedelta(minutes=1),
            Decimal("100"),
            Decimal("1"),
        )
    ])
    futures.add_many([
        HistoricalTrade(
            now.replace(second=40) - timedelta(minutes=1),
            Decimal("100"),
            Decimal("1"),
        )
    ])

    both = build_historical_snapshot(
        symbol="BTCUSDT",
        timestamp=now,
        start_timestamp=now,
        spot_window=spot,
        futures_window=futures,
        technicals={},
    )

    stale_futures = RollingTradeWindow()
    stale_futures.add_many([
        HistoricalTrade(
            now - timedelta(seconds=61),
            Decimal("100"),
            Decimal("1"),
        )
    ])
    one = build_historical_snapshot(
        symbol="BTCUSDT",
        timestamp=now,
        start_timestamp=now,
        spot_window=spot,
        futures_window=stale_futures,
        technicals={},
    )

    assert both.market_data_quality == Decimal("1")
    assert both.spot_trade_sources == 1
    assert both.futures_trade_sources == 1
    assert one.market_data_quality == Decimal("0.5")
    assert one.spot_trade_sources == 1
    assert one.futures_trade_sources == 0
    assert both.liquidation_data_available is False


def test_minute_buckets_preserve_cvd_and_ratio() -> None:
    now = datetime(2026, 10, 9, 12, 5, tzinfo=UTC)
    window = RollingTradeWindow()
    window.add_many([
        HistoricalTrade(
            datetime(2026, 10, 9, 12, 4, 10, tzinfo=UTC),
            Decimal("100"),
            Decimal("2"),
        ),
        HistoricalTrade(
            datetime(2026, 10, 9, 12, 4, 40, tzinfo=UTC),
            Decimal("101"),
            Decimal("-1"),
        ),
        HistoricalTrade(
            datetime(2026, 10, 9, 12, 5, 0, tzinfo=UTC),
            Decimal("102"),
            Decimal("3"),
        ),
    ])

    assert window.cvd(now, 1) == Decimal("4")
    assert window.ratio(now, 1) == Decimal("4") / Decimal("6")

def test_materializer_emits_one_row_per_minute(tmp_path: Path) -> None:
    symbol = "BTCUSDT"
    day = date(2026, 10, 9)
    stamp = day.isoformat()
    base = tmp_path / symbol

    spot_path = base / "spot" / "aggTrades" / f"{symbol}-aggTrades-{stamp}.zip"
    futures_path = (
        base
        / "futures"
        / "um"
        / "aggTrades"
        / f"{symbol}-aggTrades-{stamp}.zip"
    )

    first_minute_us = int(
        datetime(2026, 10, 9, 0, 0, 30, tzinfo=UTC).timestamp() * 1_000_000
    )
    first_minute_ms = int(
        datetime(2026, 10, 9, 0, 0, 35, tzinfo=UTC).timestamp() * 1_000
    )
    _write_zip(
        spot_path,
        [f"1,100,1,1,1,{first_minute_us},false,true"],
    )
    _write_zip(
        futures_path,
        [f"1,100,2,1,1,{first_minute_ms},false"],
    )

    for interval in ("5m", "15m", "1h", "4h"):
        kline_path = (
            base
            / "spot"
            / "klines"
            / interval
            / f"{symbol}-{interval}-{stamp}.zip"
        )
        _write_zip(kline_path, [])

    output = tmp_path / "features.jsonl"
    result = materialize(
        symbol=symbol,
        days=1,
        end_day=day,
        input_root=tmp_path,
        output_path=output,
    )

    lines = result.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1440

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["price"] is None
    assert second["price"] == "100"
    assert second["market_data_quality"] == "1"
    assert second["spot_trade_sources"] == 1
    assert second["futures_trade_sources"] == 1
    assert second["dataset_provenance"] == (
        "binance_vision_historical_compatible_v1"
    )
