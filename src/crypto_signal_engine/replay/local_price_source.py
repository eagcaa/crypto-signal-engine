import csv
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path
from typing import Iterable

from crypto_signal_engine.replay.models import ReplayPricePoint


def _archive_timestamp_to_datetime(value: str) -> datetime:
    raw = int(value)
    if raw <= 0:
        raise ValueError("archive timestamp must be positive")
    divisor = 1_000_000 if raw >= 100_000_000_000_000 else 1_000
    parsed = datetime.fromtimestamp(raw / divisor, tz=UTC)
    if parsed.year < 2017 or parsed.year > 2100:
        raise ValueError(f"archive timestamp out of supported range: {raw}")
    return parsed


def _spot_aggtrade_path(
    root: Path,
    *,
    symbol: str,
    day: date,
) -> Path:
    stamp = day.isoformat()
    symbol = symbol.upper()
    return (
        root
        / symbol
        / "spot"
        / "aggTrades"
        / f"{symbol}-aggTrades-{stamp}.zip"
    )


def _open_csv_rows(path: Path):
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(".csv")
        ]
        if len(members) != 1:
            raise ValueError(
                f"Expected exactly one CSV in {path}; found {len(members)}"
            )
        with archive.open(members[0]) as raw:
            with TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                yield from csv.reader(text)


def _merge_windows(
    windows: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted(windows, key=lambda item: item[0])
    merged: list[list[datetime]] = []

    for start, end in ordered:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("prediction windows must be timezone-aware")
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        if end < start:
            raise ValueError("prediction window end cannot precede start")

        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end

    return [(start, end) for start, end in merged]


class LocalBinanceSpotAggTradePriceSource:
    """Read exact Binance spot aggTrades from local Binance Vision ZIPs."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch_price_points_for_windows(
        self,
        symbol: str,
        *,
        windows: Iterable[tuple[datetime, datetime]],
    ) -> list[ReplayPricePoint]:
        merged = _merge_windows(windows)
        if not merged:
            return []

        symbol = symbol.upper()
        required_days: set[date] = set()
        for start, end in merged:
            current_day = start.date()
            while current_day <= end.date():
                required_days.add(current_day)
                current_day += timedelta(days=1)

        points: list[ReplayPricePoint] = []
        window_index = 0

        for day in sorted(required_days):
            path = _spot_aggtrade_path(
                self._root,
                symbol=symbol,
                day=day,
            )
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing local Binance spot aggTrades archive: {path}"
                )

            for row in _open_csv_rows(path):
                if not row:
                    continue
                try:
                    int(row[0])
                except ValueError:
                    continue
                if len(row) < 6:
                    raise ValueError(f"Unexpected aggTrades row in {path}: {row}")

                timestamp = _archive_timestamp_to_datetime(row[5])

                while (
                    window_index < len(merged)
                    and timestamp > merged[window_index][1]
                ):
                    window_index += 1

                if window_index >= len(merged):
                    return points

                start, end = merged[window_index]
                if start < timestamp <= end:
                    points.append(
                        ReplayPricePoint(
                            symbol=symbol,
                            timestamp=timestamp,
                            price=Decimal(row[1]),
                        )
                    )

        return points
