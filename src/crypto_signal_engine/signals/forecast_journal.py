"""SQLite journal for paper-only directional forecasts and their future outcomes."""
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crypto_signal_engine.domain.candles import Candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
 id INTEGER PRIMARY KEY,
 symbol TEXT NOT NULL,
 timeframe TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 entry_price TEXT NOT NULL,
 direction TEXT NOT NULL,
 confirmation TEXT NOT NULL,
 horizon_minutes INTEGER NOT NULL,
 target_at TEXT NOT NULL,
 exit_price TEXT,
 outcome TEXT,
 UNIQUE(symbol, timeframe, observed_at, horizon_minutes)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def record(conn: sqlite3.Connection, *, symbol: str, timeframe: str,
           observed_at: datetime, price, direction: str,
           confirmation: str, horizons=(5, 15)) -> int:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if direction not in ("RISE_EXPECTED", "FALL_EXPECTED"):
        return 0  # Uncertain is not a directional prediction.
    count = 0
    for minutes in horizons:
        if minutes <= 0:
            raise ValueError("horizon must be positive")
        cursor = conn.execute(
            """INSERT OR IGNORE INTO forecasts
            (symbol, timeframe, observed_at, entry_price, direction, confirmation,
             horizon_minutes, target_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, timeframe, observed_at.astimezone(UTC).isoformat(),
             str(price), direction, confirmation, minutes,
             (observed_at + timedelta(minutes=minutes)).astimezone(UTC).isoformat()))
        count += cursor.rowcount
    conn.commit()
    return count


def settle(conn: sqlite3.Connection, symbol: str, candles: list[Candle]) -> int:
    # Match exact closed-candle timestamps; never substitute the latest price.
    closes = {c.close_time.astimezone(UTC).isoformat(): c.close
              for c in candles if c.symbol == symbol}
    pending = conn.execute(
        """SELECT id, target_at, entry_price, direction FROM forecasts
           WHERE symbol=? AND outcome IS NULL""", (symbol,)).fetchall()
    count = 0
    for ident, target_at, entry_price, direction in pending:
        exit_price = closes.get(target_at)
        if exit_price is None:
            continue
        from decimal import Decimal
        entry = Decimal(entry_price)
        if exit_price == entry:
            outcome = "FLAT"
        elif (exit_price > entry) == (direction == "RISE_EXPECTED"):
            outcome = "CORRECT"
        else:
            outcome = "WRONG"
        conn.execute("UPDATE forecasts SET exit_price=?, outcome=? WHERE id=?",
                     (str(exit_price), outcome, ident))
        count += 1
    conn.commit()
    return count


def summary(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT symbol, timeframe, horizon_minutes, confirmation,
           COUNT(*) AS settled,
           SUM(CASE WHEN outcome='CORRECT' THEN 1 ELSE 0 END) AS correct,
           SUM(CASE WHEN outcome='WRONG' THEN 1 ELSE 0 END) AS wrong,
           SUM(CASE WHEN outcome='FLAT' THEN 1 ELSE 0 END) AS flat
           FROM forecasts WHERE outcome IS NOT NULL
           GROUP BY symbol, timeframe, horizon_minutes, confirmation
           ORDER BY symbol, timeframe, horizon_minutes, confirmation"""
    ).fetchall()
