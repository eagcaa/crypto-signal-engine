"""Download actual Bybit USDT linear-perpetual closed candles for research.

Usage:
  PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_history --days 7
No API keys or trading permissions required.
"""
import argparse
import asyncio
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

from crypto_signal_engine.collectors.bybit.candles import INTERVALS, SYMBOLS

FIELDS = ("symbol", "interval", "open_time", "open", "high", "low", "close", "volume")


async def download(session, symbol, interval, start, end):
    code, minutes = INTERVALS[interval]
    step = timedelta(minutes=minutes)
    by_open = {}
    cursor = end
    while cursor > start:
        params = {"category": "linear", "symbol": symbol, "interval": code,
                  "limit": "1000", "end": str(int((cursor - timedelta(milliseconds=1)).timestamp() * 1000))}
        for attempt in range(5):
            try:
                async with session.get("https://api.bybit.com/v5/market/kline", params=params) as response:
                    payload = await response.json(content_type=None)
                    if response.status == 429 or payload.get("retCode") in (10006,):
                        raise RuntimeError("Bybit rate limit")
                    if response.status >= 400 or payload.get("retCode") != 0:
                        raise RuntimeError(f"Bybit HTTP {response.status}: {payload.get('retMsg')}")
                break
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
                if attempt == 4:
                    raise
                await asyncio.sleep(2 ** attempt)
        rows = payload.get("result", {}).get("list", [])
        if not rows:
            break
        oldest = None
        for row in rows:
            opened = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
            if oldest is None or opened < oldest:
                oldest = opened
            if start <= opened and opened + step <= end:
                by_open[opened] = {
                    "symbol": symbol, "interval": interval,
                    "open_time": opened.isoformat(),
                    "open": row[1], "high": row[2], "low": row[3],
                    "close": row[4], "volume": row[5],
                }
        if oldest is None or oldest >= cursor:
            raise RuntimeError("Bybit pagination did not progress")
        cursor = oldest
        await asyncio.sleep(0.12)
    ordered = [by_open[t] for t in sorted(by_open)]
    if not ordered:
        raise RuntimeError(f"No historical candles for {symbol}/{interval}")
    for a, b in zip(ordered, ordered[1:]):
        if datetime.fromisoformat(b["open_time"]) - datetime.fromisoformat(a["open_time"]) != step:
            raise RuntimeError(f"Missing {interval} candle for {symbol} after {a['open_time']}")
    return ordered


async def run(days, output):
    # Align to the last fully closed 15m bar; all three timeframes share this cutoff.
    now = datetime.now(UTC)
    end = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    start = end - timedelta(days=days)
    output.parent.mkdir(parents=True, exist_ok=True)
    all_rows = []
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for symbol in sorted(SYMBOLS):
            for interval in ("1m", "5m", "15m"):
                rows = await download(session, symbol, interval, start, end)
                print(f"{symbol} {interval}: {len(rows)} closed candles")
                all_rows.extend(rows)
    # Write only after all downloads succeed, never leave a misleading partial dataset.
    temp = output.with_suffix(output.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)
    temp.replace(output)
    print(f"Saved {len(all_rows)} real Bybit perpetual candles to {output}")


def main():
    parser = argparse.ArgumentParser(description="Bybit perpetual historical candle exporter")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output", type=Path, default=Path("data/bybit_perp_history.csv"))
    args = parser.parse_args()
    if not 2 <= args.days <= 90:
        parser.error("--days must be between 2 and 90")
    asyncio.run(run(args.days, args.output))


if __name__ == "__main__":
    main()
