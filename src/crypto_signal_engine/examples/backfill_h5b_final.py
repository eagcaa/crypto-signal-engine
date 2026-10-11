"""Download only the frozen H5b 2024-2025 final-test inputs.

This downloader accepts exactly 2024-01-01 through 2025-12-31.
It does not run the strategy or print any return information.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiohttp

from crypto_signal_engine.examples.backfill_h5_funding_basis import (
    BINANCE_BASE_URL,
    validate_fred_csv,
)
from crypto_signal_engine.examples.backfill_history import (
    ArchiveSpec,
    DownloadResult,
    download_archive,
)
from crypto_signal_engine.examples.h5b_final_test import (
    FINAL_END,
    FINAL_START,
    FRED_WARMUP_START,
)


def validate_final_range(
    *,
    start_day: date,
    end_day: date,
) -> None:
    if start_day != FINAL_START or end_day != FINAL_END:
        raise ValueError(
            "H5b downloader accepts only the frozen final-test range "
            f"{FINAL_START.isoformat()} through {FINAL_END.isoformat()}."
        )


def fred_dtb3_final_url() -> str:
    return (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=DTB3&cosd={FRED_WARMUP_START.isoformat()}"
        f"&coed={FINAL_END.isoformat()}"
    )


@dataclass(frozen=True, slots=True)
class Month:
    year: int
    month: int

    @property
    def stamp(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def final_months() -> tuple[Month, ...]:
    output: list[Month] = []
    year = FINAL_START.year
    month = FINAL_START.month
    while (year, month) <= (FINAL_END.year, FINAL_END.month):
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


async def download_fred_dtb3_final(
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
    async with session.get(fred_dtb3_final_url()) as response:
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
    validate_final_range(
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
        for month in final_months():
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
                    "H5B_BACKFILL "
                    f"month={month.stamp} "
                    f"dataset={result.dataset} "
                    f"status={result.status} "
                    f"bytes={result.bytes}"
                )

        fred = await download_fred_dtb3_final(
            session,
            root=root,
        )
        results.append(fred)
        print(
            "H5B_BACKFILL "
            f"dataset={fred.dataset} "
            f"status={fred.status} "
            f"bytes={fred.bytes}"
        )

    missing = [
        item.dataset + ":" + item.path
        for item in results
        if item.status == "missing"
    ]
    print(
        "H5B_BACKFILL_READY "
        f"symbol={symbol} "
        f"start={FINAL_START.isoformat()} "
        f"end={FINAL_END.isoformat()} "
        f"files={len(results)} "
        f"missing={len(missing)} "
        f"bytes={sum(item.bytes for item in results)}"
    )
    if missing:
        raise RuntimeError(
            "Missing required H5b final-test archives: "
            + ",".join(missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download frozen H5b 2024-2025 final-test inputs only."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--start-date",
        default=FINAL_START.isoformat(),
    )
    parser.add_argument(
        "--end-date",
        default=FINAL_END.isoformat(),
    )
    parser.add_argument(
        "--output-root",
        default="runtime-data/h5b-final",
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
