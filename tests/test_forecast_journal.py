from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from crypto_signal_engine.signals.forecast_journal import connect, record, settle, summary


def test_record_settle_and_deduplicate(tmp_path):
    conn = connect(tmp_path / "forecasts.sqlite3")
    start = datetime(2026, 11, 22, 14, 30, tzinfo=UTC)
    kwargs = dict(symbol="BTCUSDT", timeframe="1m", observed_at=start,
                  price=Decimal("100"), direction="FALL_EXPECTED",
                  confirmation="ALIGNED")
    assert record(conn, **kwargs) == 2
    assert record(conn, **kwargs) == 0
    candles = [
        SimpleNamespace(symbol="BTCUSDT", close_time=start + timedelta(minutes=5),
                        close=Decimal("95")),
        SimpleNamespace(symbol="BTCUSDT", close_time=start + timedelta(minutes=15),
                        close=Decimal("105")),
    ]
    assert settle(conn, "BTCUSDT", candles) == 2
    assert settle(conn, "BTCUSDT", candles) == 0
    assert summary(conn) == [("BTCUSDT", "1m", 5, "ALIGNED", 1, 1, 0, 0),
                             ("BTCUSDT", "1m", 15, "ALIGNED", 1, 0, 1, 0)]
    conn.close()


def test_uncertain_not_recorded(tmp_path):
    conn = connect(tmp_path / "journal.db")
    assert record(conn, symbol="ETHUSDT", timeframe="5m",
                  observed_at=datetime.now(UTC), price=Decimal("10"),
                  direction="UNCERTAIN", confirmation="BALANCED") == 0
    assert summary(conn) == []
    conn.close()


def test_missing_future_candle_remains_pending(tmp_path):
    conn = connect(tmp_path / "journal.db")
    start = datetime(2026, 11, 22, 14, 30, tzinfo=UTC)
    record(conn, symbol="SOLUSDT", timeframe="1m", observed_at=start,
           price=Decimal("70"), direction="RISE_EXPECTED",
           confirmation="ALIGNED", horizons=(5,))
    assert settle(conn, "SOLUSDT", []) == 0
    assert summary(conn) == []
    conn.close()
