"""Download the frozen H5 development data only.

H5 development window is 2020-01-01 through 2023-12-31 inclusive.
This module intentionally refuses to download the 2024-2025 final-test window.
"""

import argparse
import asyncio
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiohttp

from crypto_signal_engine.examples.backfill_history import (
    ArchiveSpec,
    DownloadResult,
    download_archive,
)


BINANCE_BASE_URL = "https://data.binance.vision/data"
DEVELOPMENT_START = date(2020, 1, 1)
DEVELOPMENT_END = date(2023, 12, 31)
FRED_WARMUP_START = date(2019, 12, 1)


def fred_dtb3_url() -> str:
    return (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=DTB3&cosd={FRED_WARMUP_START.isoformat()}"
        f"&coed={DEVELOPMENT_END.isoformat()}"
    )


@dataclass(frozen=True, slots=True)
class Month:
    year: int
    month: int

    @property
    def stamp(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def validate_development_range(
    *,
    start_day: date,
    end_day: date,
) -> None:
    if end_day < start_day:
        raise ValueError("end-date must be on or after start-date")
    if start_day < DEVELOPMENT_START or end_day > DEVELOPMENT_END:
        raise ValueError(
            "H5 downloader is development-only. Allowed range is "
            f"{DEVELOPMENT_START.isoformat()} through "
            f"{DEVELOPMENT_END.isoformat()}."
        )


def month_range(start_day: date, end_day: date) -> tuple[Month, ...]:
    validate_development_range(
        start_day=start_day,
        end_day=end_day,
    )
    output: list[Month] = []
    year = start_day.year
    month = start_day.month
    while (year, month) <= (end_day.year, end_day.month):
        output.append(Month(year=year, month=month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return tuple(output)


def monthly_archive_specs(
    *,
    symbol: str,
    month: Month,
) -> tuple[ArchiveSpec, ...]:
    symbol = symbol.upper()
    stamp = month.stamp
    return (
        ArchiveSpec(
            dataset="futures_um_fundingRate",
            url=(
                f"{BINANCE_BASE_URL}/futures/um/monthly/fundingRate/"
                f"{symbol}/{symbol}-fundingRate-{stamp}.zip"
            ),
            relative_path=(
                "funding-basis/futures/um/fundingRate/"
                f"{symbol}-fundingRate-{stamp}.zip"
            ),
        ),
        ArchiveSpec(
            dataset="spot_klines_1h",
            url=(
                f"{BINANCE_BASE_URL}/spot/monthly/klines/"
                f"{symbol}/1h/{symbol}-1h-{stamp}.zip"
            ),
            relative_path=(
                "funding-basis/spot/klines/1h/"
                f"{symbol}-1h-{stamp}.zip"
            ),
        ),
        ArchiveSpec(
            dataset="futures_um_klines_1h",
            url=(
                f"{BINANCE_BASE_URL}/futures/um/monthly/klines/"
                f"{symbol}/1h/{symbol}-1h-{stamp}.zip"
            ),
            relative_path=(
                "funding-basis/futures/um/klines/1h/"
                f"{symbol}-1h-{stamp}.zip"
            ),
        ),
    )


def validate_fred_csv(path: Path) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        has_date = "DATE" in fields or "observation_date" in fields
        if not has_date or "DTB3" not in fields:
            raise ValueError(
                f"Unexpected DTB3 CSV header in {path}: "
                f"{sorted(fields)}"
            )


async def download_fred_dtb3(
    session: aiohttp.ClientSession,
    *,
    root: Path,
) -> DownloadResult:
    target = root / "funding-basis/benchmark/DTB3.csv"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        validate_fred_csv(target)
        return DownloadResult(
            dataset="fred_DTB3",
            path=str(target),
            bytes=target.stat().st_size,
            status="cached",
        )

    temp = target.with_suffix(".csv.part")
    async with session.get(fred_dtb3_url()) as response:
        response.raise_for_status()
        temp.write_bytes(await response.read())

    temp.replace(target)
    validate_fred_csv(target)
    return DownloadResult(
        dataset="fred_DTB3",
        path=str(target),
        bytes=target.stat().st_size,
        status="downloaded",
    )


async def run(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    output_root: Path,
) -> None:
    validate_development_range(
        start_day=start_day,
        end_day=end_day,
    )
    symbol = symbol.upper()
    root = output_root / symbol
    root.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30,
        sock_read=120,
    )
    results: list[DownloadResult] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for month in month_range(start_day, end_day):
            for spec in monthly_archive_specs(
                symbol=symbol,
                month=month,
            ):
                result = await download_archive(
                    session,
                    spec,
                    root=root,
                )
                results.append(result)
                print(
                    "H5_BACKFILL "
                    f"month={month.stamp} "
                    f"dataset={result.dataset} "
                    f"status={result.status} "
                    f"bytes={result.bytes}"
                )

        fred = await download_fred_dtb3(
            session,
            root=root,
        )
        results.append(fred)
        print(
            "H5_BACKFILL "
            f"dataset={fred.dataset} "
            f"status={fred.status} "
            f"bytes={fred.bytes}"
        )

    missing = [
        item.dataset + ":" + item.path
        for item in results
        if item.status == "missing"
    ]
    total_bytes = sum(item.bytes for item in results)
    print(
        "H5_BACKFILL_READY "
        f"symbol={symbol} "
        f"start={start_day.isoformat()} "
        f"end={end_day.isoformat()} "
        f"files={len(results)} "
        f"missing={len(missing)} "
        f"bytes={total_bytes}"
    )
    if missing:
        raise RuntimeError(
            "Missing required H5 development archives: "
            + ",".join(missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download H5 development-only funding, spot/perp prices "
            "and FRED DTB3."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--start-date",
        default=DEVELOPMENT_START.isoformat(),
    )
    parser.add_argument(
        "--end-date",
        default=DEVELOPMENT_END.isoformat(),
    )
    parser.add_argument(
        "--output-root",
        default="runtime-data/h5",
    )
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
