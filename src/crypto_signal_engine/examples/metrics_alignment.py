"""Validate Binance Vision futures metrics timestamps against live data.

This is an exploratory alignment diagnostic. It complements the stricter
validate_metrics_alignment command by checking both open interest shifts and
the first-seen lag/mapping of archived ratio columns against persisted live
Binance derivatives snapshots.
"""

import argparse
import asyncio
import csv
import statistics
import zipfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from pathlib import Path

import aiohttp
from sqlalchemy import select

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    create_database_engine,
    create_session_factory,
)
from crypto_signal_engine.db.models import (
    ExchangeDerivativesSnapshotRow,
)
from crypto_signal_engine.examples.backfill_history import (
    archive_timestamp_to_datetime,
    daily_archive_specs,
    download_archive,
    requested_days,
)


LIVE_RATIO_FIELDS = (
    "long_short_ratio",
    "top_trader_long_short_ratio",
    "taker_buy_sell_ratio",
)
ARCHIVE_RATIO_COLUMNS = (
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
OI_SHIFTS_MINUTES = tuple(range(-15, 16))
OI_MATCH_TOLERANCE = timedelta(seconds=45)
RATIO_SEARCH_BEFORE = timedelta(minutes=15)
RATIO_SEARCH_AFTER = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    create_time: datetime
    values: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class LiveRow:
    timestamp: datetime
    values: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class OiShiftResult:
    shift_minutes: int
    samples: int
    median_abs_rel_error: float


@dataclass(frozen=True, slots=True)
class RatioLagResult:
    live_field: str
    archive_column: str
    archive_rows: int
    matched: int
    match_rate: float
    median_lag_seconds: float | None
    p90_lag_seconds: float | None


def parse_create_time(value: str) -> datetime:
    text = value.strip()
    if text.isdigit():
        return archive_timestamp_to_datetime(text)
    parsed = datetime.fromisoformat(text.replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def read_metrics_archive(path: Path) -> list[ArchiveRow]:
    rows: list[ArchiveRow] = []
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
        ]
        if len(members) != 1:
            raise ValueError(
                f"Expected one CSV in {path}; found {len(members)}"
            )
        with archive.open(members[0]) as raw:
            with TextIOWrapper(
                raw,
                encoding="utf-8",
                newline="",
            ) as text:
                for item in csv.DictReader(text):
                    values: dict[str, Decimal] = {}
                    for column in (
                        "sum_open_interest",
                        *ARCHIVE_RATIO_COLUMNS,
                    ):
                        number = _decimal(item.get(column))
                        if number is not None:
                            values[column] = number
                    rows.append(
                        ArchiveRow(
                            create_time=parse_create_time(
                                item["create_time"]
                            ),
                            values=values,
                        )
                    )
    rows.sort(key=lambda row: row.create_time)
    return rows


def duplicate_create_times(
    rows: list[ArchiveRow],
) -> list[datetime]:
    seen: set[datetime] = set()
    duplicates: list[datetime] = []
    for row in rows:
        if row.create_time in seen:
            duplicates.append(row.create_time)
        seen.add(row.create_time)
    return duplicates


def _nearest_live(
    live: list[LiveRow],
    timestamps: list[datetime],
    target: datetime,
    field: str,
) -> Decimal | None:
    lo = bisect_left(
        timestamps,
        target - OI_MATCH_TOLERANCE,
    )
    hi = bisect_right(
        timestamps,
        target + OI_MATCH_TOLERANCE,
    )
    best: tuple[timedelta, Decimal] | None = None
    for row in live[lo:hi]:
        value = row.values.get(field)
        if value is None:
            continue
        distance = abs(row.timestamp - target)
        if best is None or distance < best[0]:
            best = (distance, value)
    return best[1] if best else None


def oi_shift_results(
    archive: list[ArchiveRow],
    live: list[LiveRow],
    *,
    shifts_minutes: tuple[int, ...] = OI_SHIFTS_MINUTES,
) -> list[OiShiftResult]:
    live = sorted(
        live,
        key=lambda row: row.timestamp,
    )
    timestamps = [row.timestamp for row in live]
    results: list[OiShiftResult] = []

    for shift in shifts_minutes:
        errors: list[float] = []
        for row in archive:
            archived = row.values.get(
                "sum_open_interest"
            )
            if archived is None or archived == 0:
                continue
            observed = _nearest_live(
                live,
                timestamps,
                row.create_time
                + timedelta(minutes=shift),
                "open_interest",
            )
            if observed is None:
                continue
            errors.append(
                float(
                    abs(observed - archived)
                    / archived
                )
            )
        if errors:
            results.append(
                OiShiftResult(
                    shift_minutes=shift,
                    samples=len(errors),
                    median_abs_rel_error=statistics.median(
                        errors
                    ),
                )
            )

    return results


def ratio_lag_results(
    archive: list[ArchiveRow],
    live: list[LiveRow],
    *,
    tolerance: Decimal = Decimal("0.00006"),
) -> list[RatioLagResult]:
    results: list[RatioLagResult] = []
    ordered_live = sorted(
        live,
        key=lambda row: row.timestamp,
    )
    live_timestamps = [
        row.timestamp
        for row in ordered_live
    ]

    for live_field in LIVE_RATIO_FIELDS:
        for column in ARCHIVE_RATIO_COLUMNS:
            lags: list[float] = []
            considered = 0

            for row in archive:
                archived = row.values.get(column)
                if archived is None:
                    continue

                window_start = (
                    row.create_time
                    - RATIO_SEARCH_BEFORE
                )
                window_end = (
                    row.create_time
                    + RATIO_SEARCH_AFTER
                )
                lo = bisect_left(
                    live_timestamps,
                    window_start,
                )
                hi = bisect_right(
                    live_timestamps,
                    window_end,
                )
                window = [
                    item
                    for item in ordered_live[lo:hi]
                    if item.values.get(
                        live_field
                    ) is not None
                ]
                if not window:
                    continue

                considered += 1
                for item in window:
                    if (
                        abs(
                            item.values[live_field]
                            - archived
                        )
                        <= tolerance
                    ):
                        lags.append(
                            (
                                item.timestamp
                                - row.create_time
                            ).total_seconds()
                        )
                        break

            if considered == 0:
                continue

            ordered = sorted(lags)
            results.append(
                RatioLagResult(
                    live_field=live_field,
                    archive_column=column,
                    archive_rows=considered,
                    matched=len(lags),
                    match_rate=(
                        len(lags) / considered
                    ),
                    median_lag_seconds=(
                        statistics.median(ordered)
                        if ordered
                        else None
                    ),
                    p90_lag_seconds=(
                        ordered[
                            int(
                                0.9
                                * (len(ordered) - 1)
                            )
                        ]
                        if ordered
                        else None
                    ),
                )
            )

    return results


def recommended_delay_minutes(
    oi_results: list[OiShiftResult],
    ratio_results: list[RatioLagResult],
    *,
    minimum_match_rate: float = 0.8,
) -> tuple[int | None, list[str]]:
    notes: list[str] = []
    candidates: list[int] = []

    if oi_results:
        best = min(
            oi_results,
            key=lambda item: (
                item.median_abs_rel_error
            ),
        )
        notes.append(
            "open_interest "
            f"best_shift={best.shift_minutes:+d}m "
            f"median_rel_error="
            f"{best.median_abs_rel_error:.6f} "
            f"n={best.samples}"
        )
        candidates.append(
            max(0, best.shift_minutes)
        )
    else:
        notes.append(
            "open_interest: no overlapping live readings"
        )

    confident = [
        item
        for item in ratio_results
        if (
            item.match_rate >= minimum_match_rate
            and item.p90_lag_seconds is not None
        )
    ]
    for item in confident:
        delay = max(
            0,
            int(
                -(
                    -item.p90_lag_seconds
                    // 60
                )
            ),
        )
        candidates.append(delay)
        notes.append(
            f"{item.archive_column}"
            f"<->{item.live_field} "
            f"match_rate={item.match_rate:.2f} "
            f"p90_lag="
            f"{item.p90_lag_seconds:.0f}s "
            f"-> delay>={delay}m"
        )

    if not candidates or not confident:
        notes.append(
            "AMBIGUOUS: not enough confident "
            "matches; keep metrics excluded"
        )
        return None, notes

    return max(candidates), notes


async def _ensure_metrics(
    symbol: str,
    days: tuple[date, ...],
    root: Path,
) -> list[Path]:
    paths: list[Path] = []
    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30,
        sock_read=120,
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        for day in days:
            spec = next(
                item
                for item in daily_archive_specs(
                    symbol=symbol,
                    day=day,
                )
                if (
                    item.dataset
                    == "futures_um_metrics"
                )
            )
            result = await download_archive(
                session,
                spec,
                root=root / symbol.upper(),
            )
            print(
                "METRICS_ARCHIVE "
                f"day={day} "
                f"status={result.status}"
            )
            if result.status != "missing":
                paths.append(Path(result.path))

    return paths


async def _load_live(
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[LiveRow]:
    settings = get_settings()
    engine = create_database_engine(
        settings.database_url
    )

    try:
        session_factory = create_session_factory(
            engine
        )
        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(
                        ExchangeDerivativesSnapshotRow
                    )
                    .where(
                        ExchangeDerivativesSnapshotRow.exchange
                        == "binance",
                        ExchangeDerivativesSnapshotRow.symbol
                        == symbol.upper(),
                        ExchangeDerivativesSnapshotRow.timestamp
                        >= start,
                        ExchangeDerivativesSnapshotRow.timestamp
                        <= end,
                    )
                    .order_by(
                        ExchangeDerivativesSnapshotRow.timestamp.asc()
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    live: list[LiveRow] = []
    for row in rows:
        values = {
            name: getattr(row, name)
            for name in (
                "open_interest",
                *LIVE_RATIO_FIELDS,
            )
            if getattr(row, name) is not None
        }
        live.append(
            LiveRow(
                timestamp=row.timestamp,
                values=values,
            )
        )

    return live


async def run(
    *,
    symbol: str,
    days: int,
    end_day: date,
    root: Path,
) -> None:
    day_list = requested_days(
        days=days,
        end_day=end_day,
    )
    paths = await _ensure_metrics(
        symbol,
        day_list,
        root,
    )

    archive: list[ArchiveRow] = []
    for path in paths:
        archive.extend(
            read_metrics_archive(path)
        )
    archive.sort(
        key=lambda row: row.create_time
    )

    if not archive:
        print(
            "METRICS_ALIGNMENT "
            "no archive rows; nothing to compare"
        )
        return

    live = await _load_live(
        symbol,
        archive[0].create_time
        - timedelta(minutes=30),
        archive[-1].create_time
        + timedelta(minutes=30),
    )

    print(
        "METRICS_ALIGNMENT "
        f"symbol={symbol.upper()} "
        f"archive_rows={len(archive)} "
        f"live_rows={len(live)} "
        f"first={archive[0].create_time.isoformat()} "
        f"last={archive[-1].create_time.isoformat()}"
    )

    duplicates = duplicate_create_times(
        archive
    )
    print(
        "DUPLICATE_CREATE_TIMES "
        f"count={len(duplicates)}"
    )
    for item in duplicates[:10]:
        print(
            f"  duplicate={item.isoformat()}"
        )

    oi_results = oi_shift_results(
        archive,
        live,
    )
    print(
        "OPEN_INTEREST_SHIFT_SCAN "
        "(lower error = better alignment)"
    )
    for item in sorted(
        oi_results,
        key=lambda result: (
            result.median_abs_rel_error
        ),
    )[:5]:
        print(
            f"  shift={item.shift_minutes:+d}m "
            f"n={item.samples} "
            f"median_rel_error="
            f"{item.median_abs_rel_error:.6f}"
        )

    ratio_results = ratio_lag_results(
        archive,
        live,
    )
    print(
        "RATIO_FIRST_SEEN_LAGS "
        "(lag = first live poll showing value "
        "- create_time)"
    )
    for item in sorted(
        ratio_results,
        key=lambda result: -result.match_rate,
    ):
        median = (
            "n/a"
            if item.median_lag_seconds is None
            else f"{item.median_lag_seconds:.0f}s"
        )
        p90 = (
            "n/a"
            if item.p90_lag_seconds is None
            else f"{item.p90_lag_seconds:.0f}s"
        )
        print(
            f"  {item.archive_column:<34} "
            f"vs {item.live_field:<28} "
            f"matched={item.matched}/"
            f"{item.archive_rows} "
            f"rate={item.match_rate:.2f} "
            f"median_lag={median} "
            f"p90_lag={p90}"
        )

    delay, notes = recommended_delay_minutes(
        oi_results,
        ratio_results,
    )
    print("RECOMMENDATION")
    for note in notes:
        print(f"  {note}")

    if delay is None:
        print("  metrics_usable=false")
    else:
        print(
            "  metrics_usable=true "
            f"minimum_delay_minutes={delay} "
            "(expose archive row T to features "
            "only at T + delay or later)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--end-date",
        default="",
        help=(
            "Inclusive UTC date. Defaults to "
            "yesterday (archives publish after close)."
        ),
    )
    parser.add_argument(
        "--backfill-root",
        default="runtime-data/backfill",
    )
    args = parser.parse_args()

    end_day = (
        date.fromisoformat(args.end_date)
        if args.end_date
        else (
            datetime.now(UTC).date()
            - timedelta(days=1)
        )
    )

    asyncio.run(
        run(
            symbol=args.symbol,
            days=args.days,
            end_day=end_day,
            root=Path(args.backfill_root),
        )
    )


if __name__ == "__main__":
    main()
