"""Persistent forward-only prediction evaluation. Never places orders."""
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

HORIZONS = (5, 15, 30, 50)


def connect(path):
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    db.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, observed_at TEXT NOT NULL,
        due_at TEXT NOT NULL, horizon INTEGER NOT NULL, model TEXT NOT NULL,
        entry REAL NOT NULL, predicted REAL NOT NULL, actual REAL,
        settled_at TEXT, error_pct REAL, direction_correct INTEGER,
        UNIQUE(symbol, observed_at, horizon, model))""")
    db.commit()
    return db


def record(db, symbol, observed_at, horizon, model, entry, predicted):
    if entry <= 0 or predicted <= 0:
        raise ValueError("Invalid prediction price")
    due = observed_at + timedelta(minutes=horizon)
    db.execute("""INSERT OR IGNORE INTO predictions
        (symbol, observed_at, due_at, horizon, model, entry, predicted)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (symbol, observed_at.isoformat(), due.isoformat(), horizon, model,
         entry, predicted))
    db.commit()


async def settle(db, session, get):
    """Resolve matured forecasts against the 1-minute candle covering due time.

    Historical candles prevent a delayed polling loop from using a later ticker.
    """
    now = datetime.now(UTC)
    pending = db.execute("""SELECT id, symbol, due_at, entry, predicted FROM predictions
        WHERE actual IS NULL AND due_at <= ? ORDER BY due_at LIMIT 300""",
        (now.isoformat(),)).fetchall()
    cache = {}
    resolved = 0
    for identifier, symbol, due_iso, entry, predicted in pending:
        due = datetime.fromisoformat(due_iso)
        # The 1m candle containing the target time must be closed.
        minute = due.replace(second=0, microsecond=0)
        if minute + timedelta(minutes=1) > now:
            continue
        key = (symbol, minute)
        if key not in cache:
            start = int(minute.timestamp() * 1000)
            response = await get(session, "/v5/market/kline", {
                "category": "linear", "symbol": symbol, "interval": "1",
                "start": start, "end": start + 59_999, "limit": "1"})
            rows = response.get("list", [])
            cache[key] = (float(rows[0][4]) if rows and int(rows[0][0]) == start
                          else None)
        actual = cache[key]
        if actual is None or actual <= 0:
            continue
        correct = int((predicted > entry) == (actual > entry)) if (
            predicted != entry and actual != entry) else int(predicted == entry and actual == entry)
        error = abs(predicted - actual) / actual * 100
        db.execute("""UPDATE predictions SET actual=?, settled_at=?, error_pct=?,
            direction_correct=? WHERE id=? AND actual IS NULL""",
            (actual, now.isoformat(), error, correct, identifier))
        resolved += 1
    db.commit()
    return resolved


def report(db):
    rows = db.execute("""SELECT symbol, horizon, model, COUNT(*),
        AVG(error_pct), AVG(direction_correct) FROM predictions
        WHERE actual IS NOT NULL GROUP BY symbol, horizon, model
        ORDER BY symbol, horizon, model""").fetchall()
    if not rows:
        return "Tahmin takibi: Henuz suresi dolmus ve dogrulanmis tahmin yok."
    lines = ["TAHMIN TAKIBI | coin | sure | yontem | adet | MAE | yon isabeti"]
    for symbol, horizon, model, count, mae, accuracy in rows:
        lines.append(f"  {symbol} | {horizon}dk | {model} | {count} "
                     f"| hata=%{mae:.3f} | yon=%{accuracy * 100:.1f}")
    return "\n".join(lines)
