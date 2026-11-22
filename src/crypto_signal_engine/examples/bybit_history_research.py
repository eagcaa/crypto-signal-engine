"""Download and persist 30 days of closed Bybit USDT perpetual 1m candles.

Usage: PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_history_research --days 30
No credentials or orders.
"""
import argparse
import asyncio
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

from crypto_signal_engine.collectors.bybit.candles import BybitLinearCandleClient
from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.research.price_backtest import evaluate, read_candles

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
FIELDS = ("symbol", "interval", "open_time", "close_time", "open", "high",
          "low", "close", "volume", "quote_volume")


async def download(symbol: str, days: int, destination: Path, *,
                   now: datetime | None = None, session=None) -> int:
    now = now or datetime.now(UTC)
    # Align to the last fully closed minute; do not include a partial candle.
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported symbol")
    destination.parent.mkdir(parents=True, exist_ok=True)
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    rows = {}
    cursor = end
    try:
        while cursor > start:
            # Each request is bounded by 1000 1m candles; request only past data.
            window_start = max(start, cursor - timedelta(minutes=1000))
            params = {
                "category": "linear", "symbol": symbol, "interval": "1",
                "start": int(window_start.timestamp() * 1000),
                "end": int((cursor - timedelta(milliseconds=1)).timestamp() * 1000),
                "limit": "1000",
            }
            for attempt in range(5):
                try:
                    async with session.get(BybitLinearCandleClient.BASE_URL, params=params) as resp:
                        payload = await resp.json(content_type=None)
                        if resp.status != 200 or payload.get("retCode") != 0:
                            raise RuntimeError(f"Bybit HTTP={resp.status} retMsg={payload.get('retMsg')}")
                    break
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
                    if attempt == 4:
                        raise
                    await asyncio.sleep(2 ** attempt)
            for raw in payload.get("result", {}).get("list", []):
                opened = datetime.fromtimestamp(int(raw[0]) / 1000, UTC)
                if start <= opened < end and opened + timedelta(minutes=1) <= end:
                    rows[opened] = [symbol, "1m", opened.isoformat(),
                                    (opened + timedelta(minutes=1)).isoformat(),
                                    *raw[1:7]]
            cursor = window_start
            await asyncio.sleep(0.08)
    finally:
        if own_session:
            await session.close()
    ordered = [rows[k] for k in sorted(rows)]
    if len(ordered) != days * 1440:
        raise ValueError(f"{symbol}: incomplete history: {len(ordered)}/{days * 1440} candles; not saving")
    # Check strict continuity; fail closed on missing or duplicate minutes.
    for a, b in zip(ordered, ordered[1:]):
        if datetime.fromisoformat(b[2]) - datetime.fromisoformat(a[2]) != timedelta(minutes=1):
            raise ValueError("Non-contiguous history")
    tmp = destination.with_suffix(".tmp")
    with tmp.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(ordered)
    tmp.replace(destination)
    return len(ordered)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", default="data/bybit_history")
    parser.add_argument("--evaluate-only", action="store_true")
    args = parser.parse_args()
    for symbol in SYMBOLS:
        path = Path(args.output) / f"{symbol}_1m_{args.days}d.csv"
        if not args.evaluate_only:
            count = await download(symbol, args.days, path)
            print(f"{symbol}: saved {count} closed 1m candles to {path}")
        candles = read_candles(path)
        for result in evaluate(candles):
            print(f"{symbol} +{result['horizon']}m: "
                  f"validation model_MAE={result['validation_model_mae']:.4f}% "
                  f"naive_MAE={result['validation_naive_mae']:.4f}% "
                  f"test model_MAE={result['test_model_mae']:.4f}% "
                  f"naive_MAE={result['test_naive_mae']:.4f}% "
                  f"test_direction_accuracy={result['test_direction_accuracy']:.1f}% "
                  f"n={result['test_n']} "
                  f"status={result['status']}")


if __name__ == "__main__":
    asyncio.run(main())
