import argparse
import asyncio
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiohttp


BASE_URL = "https://data.binance.vision/data"
TECHNICAL_WARMUP_DAYS = 18
EXACT_OUTCOME_BUFFER_DAYS = 1


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    dataset: str
    url: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    dataset: str
    path: str
    bytes: int
    status: str


def kline_archive_specs(
    *,
    symbol: str,
    day: date,
) -> tuple[ArchiveSpec, ...]:
    symbol = symbol.upper()
    stamp = day.isoformat()
    return tuple(
        ArchiveSpec(
            dataset=f"spot_klines_{interval}",
            url=(
                f"{BASE_URL}/spot/daily/klines/{symbol}/{interval}/"
                f"{symbol}-{interval}-{stamp}.zip"
            ),
            relative_path=(
                f"spot/klines/{interval}/{symbol}-{interval}-{stamp}.zip"
            ),
        )
        for interval in ("5m", "15m", "1h", "4h")
    )


def daily_archive_specs(
    *,
    symbol: str,
    day: date,
) -> tuple[ArchiveSpec, ...]:
    symbol = symbol.upper()
    stamp = day.isoformat()
    specs = [
        ArchiveSpec(
            dataset="spot_aggTrades",
            url=(
                f"{BASE_URL}/spot/daily/aggTrades/{symbol}/"
                f"{symbol}-aggTrades-{stamp}.zip"
            ),
            relative_path=f"spot/aggTrades/{symbol}-aggTrades-{stamp}.zip",
        ),
        ArchiveSpec(
            dataset="futures_um_aggTrades",
            url=(
                f"{BASE_URL}/futures/um/daily/aggTrades/{symbol}/"
                f"{symbol}-aggTrades-{stamp}.zip"
            ),
            relative_path=(
                f"futures/um/aggTrades/{symbol}-aggTrades-{stamp}.zip"
            ),
        ),
        ArchiveSpec(
            dataset="futures_um_metrics",
            url=(
                f"{BASE_URL}/futures/um/daily/metrics/{symbol}/"
                f"{symbol}-metrics-{stamp}.zip"
            ),
            relative_path=f"futures/um/metrics/{symbol}-metrics-{stamp}.zip",
        ),
    ]
    specs.extend(kline_archive_specs(symbol=symbol, day=day))
    return tuple(specs)



def exact_outcome_archive_specs(
    *,
    symbol: str,
    day: date,
) -> tuple[ArchiveSpec, ...]:
    """Spot aggTrades needed only to finish first-touch outcomes past end_day."""
    symbol = symbol.upper()
    stamp = day.isoformat()
    return (
        ArchiveSpec(
            dataset="spot_aggTrades_outcome_buffer",
            url=(
                f"{BASE_URL}/spot/daily/aggTrades/{symbol}/"
                f"{symbol}-aggTrades-{stamp}.zip"
            ),
            relative_path=f"spot/aggTrades/{symbol}-aggTrades-{stamp}.zip",
        ),
    )

def archive_timestamp_to_datetime(value: int | str) -> datetime:
    raw = int(value)
    if raw <= 0:
        raise ValueError("archive timestamp must be positive")

    # Binance spot archives may use microseconds while futures archives use
    # milliseconds. Infer the unit by magnitude, then sanity-check the result.
    divisor = 1_000_000 if raw >= 100_000_000_000_000 else 1_000
    parsed = datetime.fromtimestamp(raw / divisor, tz=UTC)
    if parsed.year < 2017 or parsed.year > 2100:
        raise ValueError(
            f"archive timestamp out of supported range: {raw}"
        )
    return parsed


def requested_days(
    *,
    days: int,
    end_day: date,
) -> tuple[date, ...]:
    if days <= 0:
        raise ValueError("days must be positive")
    start_day = end_day - timedelta(days=days - 1)
    return tuple(
        start_day + timedelta(days=offset)
        for offset in range(days)
    )


def validate_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(
                f"Corrupt ZIP member {bad_member!r} in {path}"
            )


async def download_archive(
    session: aiohttp.ClientSession,
    spec: ArchiveSpec,
    *,
    root: Path,
) -> DownloadResult:
    target = root / spec.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        validate_zip(target)
        return DownloadResult(
            dataset=spec.dataset,
            path=str(target),
            bytes=target.stat().st_size,
            status="cached",
        )

    temp = target.with_suffix(target.suffix + ".part")
    async with session.get(spec.url) as response:
        if response.status == 404:
            return DownloadResult(
                dataset=spec.dataset,
                path=str(target),
                bytes=0,
                status="missing",
            )
        response.raise_for_status()
        with temp.open("wb") as handle:
            async for chunk in response.content.iter_chunked(1024 * 1024):
                handle.write(chunk)

    temp.replace(target)
    validate_zip(target)
    return DownloadResult(
        dataset=spec.dataset,
        path=str(target),
        bytes=target.stat().st_size,
        status="downloaded",
    )


async def run(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_root: Path,
) -> None:
    days_to_fetch = requested_days(days=days, end_day=end_day)
    root = output_root / symbol.upper()
    root.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
    results: list[DownloadResult] = []

    warmup_end = days_to_fetch[0] - timedelta(days=1)
    warmup_days = requested_days(
        days=TECHNICAL_WARMUP_DAYS,
        end_day=warmup_end,
    )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for current_day in warmup_days:
            print(f"BACKFILL_WARMUP_KLINES day={current_day.isoformat()}")
            for spec in kline_archive_specs(
                symbol=symbol,
                day=current_day,
            ):
                result = await download_archive(
                    session,
                    spec,
                    root=root,
                )
                results.append(result)
                print(
                    f"  dataset={result.dataset} "
                    f"status={result.status} "
                    f"bytes={result.bytes}"
                )

        for current_day in days_to_fetch:
            print(f"BACKFILL_DOWNLOAD day={current_day.isoformat()}")
            for spec in daily_archive_specs(
                symbol=symbol,
                day=current_day,
            ):
                result = await download_archive(
                    session,
                    spec,
                    root=root,
                )
                results.append(result)
                print(
                    f"  dataset={result.dataset} "
                    f"status={result.status} "
                    f"bytes={result.bytes}"
                )

        for offset in range(1, EXACT_OUTCOME_BUFFER_DAYS + 1):
            current_day = days_to_fetch[-1] + timedelta(days=offset)
            print(
                "BACKFILL_OUTCOME_BUFFER "
                f"day={current_day.isoformat()}"
            )
            for spec in exact_outcome_archive_specs(
                symbol=symbol,
                day=current_day,
            ):
                result = await download_archive(
                    session,
                    spec,
                    root=root,
                )
                results.append(result)
                print(
                    f"  dataset={result.dataset} "
                    f"status={result.status} "
                    f"bytes={result.bytes}"
                )

    manifest = {
        "schema_version": 1,
        "symbol": symbol.upper(),
        "generated_at": datetime.now(UTC).isoformat(),
        "start_day": days_to_fetch[0].isoformat(),
        "end_day": days_to_fetch[-1].isoformat(),
        "days": len(days_to_fetch),
        "feature_snapshot_cadence_seconds": 60,
        "technical_warmup_days": TECHNICAL_WARMUP_DAYS,
        "exact_outcome_buffer_days": EXACT_OUTCOME_BUFFER_DAYS,
        "dataset_provenance": "binance_vision_historical_compatible_v1",
        "files": [asdict(item) for item in results],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    total_bytes = sum(item.bytes for item in results)
    missing = sum(1 for item in results if item.status == "missing")
    print(
        "BACKFILL_READY "
        f"symbol={symbol.upper()} "
        f"days={len(days_to_fetch)} "
        f"files={len(results)} "
        f"missing={missing} "
        f"bytes={total_bytes} "
        f"manifest={manifest_path}"
    )
    print(
        "NOTE feature materialization is intentionally 60s cadence; "
        "raw aggTrades remain tick-level for later exact first-touch replay, "
        "including the post-period outcome buffer day."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and validate Binance Vision history for backfill."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--end-date",
        default="",
        help=(
            "Inclusive UTC YYYY-MM-DD. Defaults to yesterday because "
            "Binance daily archives are published after the day closes."
        ),
    )
    parser.add_argument(
        "--output-root",
        default="runtime-data/backfill",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end_day = (
        date.fromisoformat(args.end_date)
        if args.end_date
        else datetime.now(UTC).date() - timedelta(days=1)
    )
    asyncio.run(
        run(
            symbol=args.symbol,
            days=args.days,
            end_day=end_day,
            output_root=Path(args.output_root),
        )
    )


if __name__ == "__main__":
    main()
