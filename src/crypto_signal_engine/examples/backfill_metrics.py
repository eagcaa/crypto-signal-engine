import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

import aiohttp

from crypto_signal_engine.examples.backfill_history import (
    ArchiveSpec,
    download_archive,
)


BASE_URL = "https://data.binance.vision/data"


def metrics_archive_spec(*, symbol: str, day: date) -> ArchiveSpec:
    symbol = symbol.upper()
    stamp = day.isoformat()
    return ArchiveSpec(
        dataset="futures_um_metrics",
        url=(
            f"{BASE_URL}/futures/um/daily/metrics/{symbol}/"
            f"{symbol}-metrics-{stamp}.zip"
        ),
        relative_path=(
            f"futures/um/metrics/{symbol}-metrics-{stamp}.zip"
        ),
    )


def date_range(start_day: date, end_day: date) -> tuple[date, ...]:
    if end_day < start_day:
        raise ValueError("end-date must be on or after start-date")
    count = (end_day - start_day).days + 1
    return tuple(start_day + timedelta(days=i) for i in range(count))


async def run(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    output_root: Path,
) -> None:
    root = output_root / symbol.upper()
    days = date_range(start_day, end_day)
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
    missing: list[str] = []
    total_bytes = 0

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for day in days:
            spec = metrics_archive_spec(symbol=symbol, day=day)
            result = await download_archive(session, spec, root=root)
            total_bytes += result.bytes
            print(
                "METRICS_BACKFILL "
                f"day={day.isoformat()} "
                f"status={result.status} "
                f"bytes={result.bytes}"
            )
            if result.status == "missing":
                missing.append(day.isoformat())

    print(
        "METRICS_BACKFILL_READY "
        f"symbol={symbol.upper()} "
        f"days={len(days)} "
        f"missing={len(missing)} "
        f"bytes={total_bytes}"
    )

    if missing:
        raise RuntimeError(
            "Missing Binance Vision metrics archives for: "
            + ",".join(missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download only Binance Vision USD-M metrics archives."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-root", default="runtime-data/backfill")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run(
            symbol=args.symbol,
            start_day=date.fromisoformat(args.start_date),
            end_day=date.fromisoformat(args.end_date),
            output_root=Path(args.output_root),
        )
    )


if __name__ == "__main__":
    main()
