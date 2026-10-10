import csv
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path

from crypto_signal_engine.examples.backfill_history import (
    archive_timestamp_to_datetime,
)


METRICS_FRESHNESS_SECONDS = 360


@dataclass(frozen=True, slots=True)
class ArchivedMetricPoint:
    timestamp: datetime
    open_interest: Decimal
    open_interest_value: Decimal | None
    long_short_ratio: Decimal | None
    top_trader_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None


@dataclass(frozen=True, slots=True)
class HistoricalMetricFeatures:
    oi_change_5m_pct: Decimal | None
    oi_change_15m_pct: Decimal | None
    long_short_ratio: Decimal | None
    top_trader_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None


def _normalize_header(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def _parse_timestamp(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("metrics timestamp is empty")

    if raw.isdigit():
        return archive_timestamp_to_datetime(raw)

    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return Decimal(stripped)


def _required_field(
    row: dict[str, str],
    *names: str,
) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            return value
    raise ValueError(
        "Required metrics field missing; tried: " + ", ".join(names)
    )


def load_metrics_archive(path: Path) -> list[ArchivedMetricPoint]:
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(".csv")
        ]
        if len(members) != 1:
            raise ValueError(
                f"Expected exactly one metrics CSV in {path}; "
                f"found {len(members)}"
            )

        with archive.open(members[0]) as raw:
            with TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                reader = csv.DictReader(text)
                if not reader.fieldnames:
                    raise ValueError(f"Metrics CSV has no header: {path}")

                reader.fieldnames = [
                    _normalize_header(name)
                    for name in reader.fieldnames
                ]

                points: list[ArchivedMetricPoint] = []
                for row in reader:
                    normalized = {
                        _normalize_header(str(key)): value
                        for key, value in row.items()
                        if key is not None
                    }
                    points.append(
                        ArchivedMetricPoint(
                            timestamp=_parse_timestamp(
                                _required_field(
                                    normalized,
                                    "create_time",
                                    "timestamp",
                                    "time",
                                )
                            ),
                            open_interest=Decimal(
                                _required_field(
                                    normalized,
                                    "sum_open_interest",
                                    "open_interest",
                                )
                            ),
                            open_interest_value=_decimal_or_none(
                                normalized.get("sum_open_interest_value")
                            ),
                            long_short_ratio=_decimal_or_none(
                                normalized.get("count_long_short_ratio")
                                or normalized.get("long_short_ratio")
                            ),
                            top_trader_long_short_ratio=_decimal_or_none(
                                normalized.get(
                                    "count_toptrader_long_short_ratio"
                                )
                                or normalized.get(
                                    "top_trader_long_short_ratio"
                                )
                            ),
                            taker_buy_sell_ratio=_decimal_or_none(
                                normalized.get(
                                    "sum_taker_long_short_vol_ratio"
                                )
                                or normalized.get("taker_buy_sell_ratio")
                            ),
                        )
                    )

    return points


def load_metrics_range(
    *,
    root: Path,
    symbol: str,
    start_day: date,
    end_day: date,
) -> list[ArchivedMetricPoint]:
    if end_day < start_day:
        raise ValueError("end_day must be on or after start_day")

    symbol = symbol.upper()
    points: list[ArchivedMetricPoint] = []
    current = start_day

    while current <= end_day:
        stamp = current.isoformat()
        path = (
            root
            / symbol
            / "futures"
            / "um"
            / "metrics"
            / f"{symbol}-metrics-{stamp}.zip"
        )
        if not path.exists():
            raise FileNotFoundError(
                f"Missing Binance Vision metrics archive: {path}"
            )
        points.extend(load_metrics_archive(path))
        current += timedelta(days=1)

    points.sort(key=lambda item: item.timestamp)
    return points


def count_duplicate_timestamps(
    points: list[ArchivedMetricPoint],
) -> int:
    duplicates = 0
    previous: datetime | None = None
    for point in sorted(points, key=lambda item: item.timestamp):
        if previous == point.timestamp:
            duplicates += 1
        previous = point.timestamp
    return duplicates


def _change_pct(
    newer: Decimal | None,
    older: Decimal | None,
) -> Decimal | None:
    if newer is None or older in (None, Decimal("0")):
        return None
    return ((newer - older) / older) * Decimal("100")


class HistoricalMetricsTimeline:
    def __init__(
        self,
        points: list[ArchivedMetricPoint],
        *,
        observable_shift_minutes: int,
    ) -> None:
        duplicates = count_duplicate_timestamps(points)
        if duplicates:
            raise ValueError(
                f"Duplicate metrics timestamps are not allowed: {duplicates}"
            )

        self._points = sorted(
            (
                (
                    point.timestamp
                    + timedelta(minutes=observable_shift_minutes),
                    point,
                )
                for point in points
            ),
            key=lambda item: item[0],
        )
        self._cursor = 0
        self._history: deque[
            tuple[datetime, ArchivedMetricPoint]
        ] = deque()

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=20)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _point_at_or_before(
        self,
        target: datetime,
    ) -> ArchivedMetricPoint | None:
        for observable_at, point in reversed(self._history):
            if observable_at <= target:
                return point
        return None

    def at(self, now: datetime) -> HistoricalMetricFeatures | None:
        while (
            self._cursor < len(self._points)
            and self._points[self._cursor][0] <= now
        ):
            self._history.append(self._points[self._cursor])
            self._cursor += 1

        self._trim(now)
        if not self._history:
            return None

        latest_observable_at, latest = self._history[-1]
        age_seconds = (now - latest_observable_at).total_seconds()
        if not 0 <= age_seconds <= METRICS_FRESHNESS_SECONDS:
            return None

        five_minutes_ago = self._point_at_or_before(
            latest_observable_at - timedelta(minutes=5)
        )
        fifteen_minutes_ago = self._point_at_or_before(
            latest_observable_at - timedelta(minutes=15)
        )

        return HistoricalMetricFeatures(
            oi_change_5m_pct=_change_pct(
                latest.open_interest_value,
                (
                    five_minutes_ago.open_interest_value
                    if five_minutes_ago is not None
                    else None
                ),
            ),
            oi_change_15m_pct=_change_pct(
                latest.open_interest_value,
                (
                    fifteen_minutes_ago.open_interest_value
                    if fifteen_minutes_ago is not None
                    else None
                ),
            ),
            long_short_ratio=latest.long_short_ratio,
            top_trader_long_short_ratio=(
                latest.top_trader_long_short_ratio
            ),
            taker_buy_sell_ratio=latest.taker_buy_sell_ratio,
        )
