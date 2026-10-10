import argparse
import asyncio
import csv
import json
import statistics
import zipfile
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path

from sqlalchemy import select

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db.models import ExchangeDerivativesSnapshotRow
from crypto_signal_engine.db.session import (
    create_database_engine,
    create_session_factory,
)
from crypto_signal_engine.examples.backfill_history import (
    archive_timestamp_to_datetime,
)


SHIFT_CANDIDATES_MINUTES = (-5, 0, 5)
MAX_NEAREST_LIVE_SECONDS = 90
MAX_PASS_MEDIAN_RELATIVE_ERROR = Decimal("0.005")
MIN_MATCHES = 10
CLEAR_WIN_RATIO = Decimal("0.75")


@dataclass(frozen=True, slots=True)
class ArchivedMetricPoint:
    timestamp: datetime
    open_interest: Decimal
    open_interest_value: Decimal | None
    long_short_ratio: Decimal | None
    top_trader_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None


@dataclass(frozen=True, slots=True)
class LiveOiPoint:
    timestamp: datetime
    open_interest: Decimal


@dataclass(frozen=True, slots=True)
class AlignmentScore:
    shift_minutes: int
    matches: int
    median_relative_error: Decimal | None
    p90_relative_error: Decimal | None


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    status: str
    selected_shift_minutes: int | None
    archive_points: int
    live_points: int
    duplicate_timestamps: int
    scores: tuple[AlignmentScore, ...]
    reason: str


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
                    timestamp = _parse_timestamp(
                        _required_field(
                            normalized,
                            "create_time",
                            "timestamp",
                            "time",
                        )
                    )
                    open_interest = Decimal(
                        _required_field(
                            normalized,
                            "sum_open_interest",
                            "open_interest",
                        )
                    )
                    points.append(
                        ArchivedMetricPoint(
                            timestamp=timestamp,
                            open_interest=open_interest,
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


def _nearest_live(
    *,
    target: datetime,
    live_points: list[LiveOiPoint],
    live_timestamps: list[datetime],
) -> LiveOiPoint | None:
    index = bisect_left(live_timestamps, target)
    candidates: list[LiveOiPoint] = []
    if index < len(live_points):
        candidates.append(live_points[index])
    if index > 0:
        candidates.append(live_points[index - 1])
    if not candidates:
        return None

    nearest = min(
        candidates,
        key=lambda item: abs((item.timestamp - target).total_seconds()),
    )
    delta = abs((nearest.timestamp - target).total_seconds())
    if delta > MAX_NEAREST_LIVE_SECONDS:
        return None
    return nearest


def _percentile(values: list[Decimal], percentile: float) -> Decimal:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def score_shift(
    archive_points: list[ArchivedMetricPoint],
    live_points: list[LiveOiPoint],
    *,
    shift_minutes: int,
) -> AlignmentScore:
    live_points = sorted(live_points, key=lambda item: item.timestamp)
    live_timestamps = [item.timestamp for item in live_points]
    errors: list[Decimal] = []

    for archived in archive_points:
        target = archived.timestamp + timedelta(minutes=shift_minutes)
        live = _nearest_live(
            target=target,
            live_points=live_points,
            live_timestamps=live_timestamps,
        )
        if live is None:
            continue

        denominator = max(abs(archived.open_interest), Decimal("0.00000001"))
        errors.append(
            abs(live.open_interest - archived.open_interest) / denominator
        )

    if not errors:
        return AlignmentScore(
            shift_minutes=shift_minutes,
            matches=0,
            median_relative_error=None,
            p90_relative_error=None,
        )

    return AlignmentScore(
        shift_minutes=shift_minutes,
        matches=len(errors),
        median_relative_error=Decimal(str(statistics.median(errors))),
        p90_relative_error=_percentile(errors, 0.90),
    )


def evaluate_alignment(
    archive_points: list[ArchivedMetricPoint],
    live_points: list[LiveOiPoint],
) -> AlignmentResult:
    duplicate_count = count_duplicate_timestamps(archive_points)
    scores = tuple(
        score_shift(
            archive_points,
            live_points,
            shift_minutes=shift,
        )
        for shift in SHIFT_CANDIDATES_MINUTES
    )

    usable = [
        score
        for score in scores
        if score.matches >= MIN_MATCHES
        and score.median_relative_error is not None
    ]

    if duplicate_count:
        return AlignmentResult(
            status="FAIL",
            selected_shift_minutes=None,
            archive_points=len(archive_points),
            live_points=len(live_points),
            duplicate_timestamps=duplicate_count,
            scores=scores,
            reason="duplicate_or_boundary_metric_timestamps",
        )

    if not usable:
        return AlignmentResult(
            status="FAIL",
            selected_shift_minutes=None,
            archive_points=len(archive_points),
            live_points=len(live_points),
            duplicate_timestamps=0,
            scores=scores,
            reason="insufficient_live_archive_matches",
        )

    ranked = sorted(
        usable,
        key=lambda item: item.median_relative_error
        if item.median_relative_error is not None
        else Decimal("Infinity"),
    )
    best = ranked[0]

    if (
        best.median_relative_error is None
        or best.median_relative_error > MAX_PASS_MEDIAN_RELATIVE_ERROR
    ):
        return AlignmentResult(
            status="FAIL",
            selected_shift_minutes=None,
            archive_points=len(archive_points),
            live_points=len(live_points),
            duplicate_timestamps=0,
            scores=scores,
            reason="archive_oi_does_not_match_live_oi",
        )

    if len(ranked) > 1:
        second = ranked[1]
        assert second.median_relative_error is not None
        clearly_better = (
            second.median_relative_error == 0
            and best.median_relative_error == 0
        ) is False and (
            best.median_relative_error
            <= second.median_relative_error * CLEAR_WIN_RATIO
        )
        if not clearly_better:
            return AlignmentResult(
                status="AMBIGUOUS",
                selected_shift_minutes=None,
                archive_points=len(archive_points),
                live_points=len(live_points),
                duplicate_timestamps=0,
                scores=scores,
                reason="multiple_timestamp_shifts_fit_similarly",
            )

    return AlignmentResult(
        status="PASS",
        selected_shift_minutes=best.shift_minutes,
        archive_points=len(archive_points),
        live_points=len(live_points),
        duplicate_timestamps=0,
        scores=scores,
        reason="unique_low_error_alignment",
    )


async def load_live_oi(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
) -> list[LiveOiPoint]:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    start = datetime.combine(start_day, time.min, tzinfo=UTC) - timedelta(
        minutes=10
    )
    end = (
        datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=UTC)
        + timedelta(minutes=10)
    )

    try:
        async with session_factory() as session:
            statement = (
                select(
                    ExchangeDerivativesSnapshotRow.timestamp,
                    ExchangeDerivativesSnapshotRow.open_interest,
                )
                .where(
                    ExchangeDerivativesSnapshotRow.exchange == "binance",
                    ExchangeDerivativesSnapshotRow.symbol == symbol.upper(),
                    ExchangeDerivativesSnapshotRow.timestamp >= start,
                    ExchangeDerivativesSnapshotRow.timestamp < end,
                    ExchangeDerivativesSnapshotRow.open_interest.is_not(None),
                )
                .order_by(ExchangeDerivativesSnapshotRow.timestamp)
            )
            rows = (await session.execute(statement)).all()
            return [
                LiveOiPoint(timestamp=row.timestamp, open_interest=row.open_interest)
                for row in rows
                if row.open_interest is not None
            ]
    finally:
        await engine.dispose()


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


async def run(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    input_root: Path,
    output_path: Path | None,
) -> int:
    archive_points = load_metrics_range(
        root=input_root,
        symbol=symbol,
        start_day=start_day,
        end_day=end_day,
    )
    live_points = await load_live_oi(
        symbol=symbol,
        start_day=start_day,
        end_day=end_day,
    )
    result = evaluate_alignment(archive_points, live_points)

    print(
        "METRICS_ALIGNMENT "
        f"status={result.status} "
        f"archive_points={result.archive_points} "
        f"live_points={result.live_points} "
        f"duplicates={result.duplicate_timestamps} "
        f"selected_shift_minutes={result.selected_shift_minutes} "
        f"reason={result.reason}"
    )
    for score in result.scores:
        median = (
            "n/a"
            if score.median_relative_error is None
            else f"{score.median_relative_error:.8f}"
        )
        p90 = (
            "n/a"
            if score.p90_relative_error is None
            else f"{score.p90_relative_error:.8f}"
        )
        print(
            "METRICS_ALIGNMENT_SHIFT "
            f"shift_minutes={score.shift_minutes:+d} "
            f"matches={score.matches} "
            f"median_rel_error={median} "
            f"p90_rel_error={p90}"
        )

    if output_path is None:
        output_dir = Path("runtime-data/metrics-alignment")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / (
            f"{symbol.upper()}-{start_day.isoformat()}-"
            f"{end_day.isoformat()}.json"
        )
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        **asdict(result),
        "symbol": symbol.upper(),
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "rules": {
            "shift_candidates_minutes": list(SHIFT_CANDIDATES_MINUTES),
            "max_nearest_live_seconds": MAX_NEAREST_LIVE_SECONDS,
            "max_pass_median_relative_error": str(
                MAX_PASS_MEDIAN_RELATIVE_ERROR
            ),
            "minimum_matches": MIN_MATCHES,
            "clear_win_ratio": str(CLEAR_WIN_RATIO),
        },
    }
    output_path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"METRICS_ALIGNMENT_ARTIFACT path={output_path}")

    return 0 if result.status == "PASS" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Binance Vision USD-M metrics OI timestamps against "
            "persisted live Binance OI without using strategy outcomes."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--input-root", default="runtime-data/backfill")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                symbol=args.symbol,
                start_day=date.fromisoformat(args.start_date),
                end_day=date.fromisoformat(args.end_date),
                input_root=Path(args.input_root),
                output_path=Path(args.output) if args.output else None,
            )
        )
    )


if __name__ == "__main__":
    main()
