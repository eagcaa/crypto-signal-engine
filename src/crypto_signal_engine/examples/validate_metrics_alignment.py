import argparse
import asyncio
import json
import statistics
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db.models import ExchangeDerivativesSnapshotRow
from crypto_signal_engine.db.session import (
    create_database_engine,
    create_session_factory,
)
from crypto_signal_engine.features.historical_metrics import (
    ArchivedMetricPoint,
    count_duplicate_timestamps,
    load_metrics_range,
)
from crypto_signal_engine.examples.metrics_alignment import (
    ArchiveRow,
    LiveRow,
    RatioLagResult,
    ratio_lag_results,
    read_metrics_archive,
)


SHIFT_CANDIDATES_MINUTES = (-5, 0, 5)
MAX_NEAREST_LIVE_SECONDS = 90
MAX_PASS_MEDIAN_RELATIVE_ERROR = Decimal("0.005")
MIN_MATCHES = 10
CLEAR_WIN_RATIO = Decimal("0.75")
MIN_RATIO_MATCH_RATE = 0.80
MIN_RATIO_MAPPING_MARGIN = 0.10


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
    ratio_delay_minutes: int | None
    ratio_mappings: tuple[RatioLagResult, ...]
    reason: str


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

        denominator = max(
            abs(archived.open_interest),
            Decimal("0.00000001"),
        )
        errors.append(
            abs(live.open_interest - archived.open_interest)
            / denominator
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
        median_relative_error=Decimal(
            str(statistics.median(errors))
        ),
        p90_relative_error=_percentile(errors, 0.90),
    )



def _best_ratio_mappings(
    results: list[RatioLagResult],
) -> tuple[tuple[RatioLagResult, ...], str | None]:
    selected: list[RatioLagResult] = []

    for live_field in (
        "long_short_ratio",
        "top_trader_long_short_ratio",
        "taker_buy_sell_ratio",
    ):
        candidates = sorted(
            (
                item
                for item in results
                if item.live_field == live_field
                and item.p90_lag_seconds is not None
            ),
            key=lambda item: (
                -item.match_rate,
                item.p90_lag_seconds
                if item.p90_lag_seconds is not None
                else float("inf"),
            ),
        )
        if not candidates:
            return (), f"ratio_mapping_missing:{live_field}"

        best = candidates[0]
        if best.match_rate < MIN_RATIO_MATCH_RATE:
            return (), f"ratio_match_rate_low:{live_field}"

        if len(candidates) > 1:
            second = candidates[1]
            if (
                second.match_rate >= MIN_RATIO_MATCH_RATE
                and best.match_rate - second.match_rate
                < MIN_RATIO_MAPPING_MARGIN
            ):
                return (), f"ratio_mapping_ambiguous:{live_field}"

        selected.append(best)

    return tuple(selected), None


def _ratio_delay_minutes(
    mappings: tuple[RatioLagResult, ...],
) -> int:
    delays = [
        max(
            0,
            int(
                -(
                    -float(item.p90_lag_seconds)
                    // 60
                )
            ),
        )
        for item in mappings
        if item.p90_lag_seconds is not None
    ]
    return max(delays, default=0)


def evaluate_alignment(
    archive_points: list[ArchivedMetricPoint],
    live_points: list[LiveOiPoint],
    ratio_results: list[RatioLagResult] | None = None,
) -> AlignmentResult:
    duplicate_count = count_duplicate_timestamps(archive_points)
    ratio_results = ratio_results or []
    ratio_mappings, ratio_error = _best_ratio_mappings(ratio_results)
    ratio_delay = (
        _ratio_delay_minutes(ratio_mappings)
        if ratio_error is None
        else None
    )
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
            ratio_delay_minutes=ratio_delay,
            ratio_mappings=ratio_mappings,
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
            ratio_delay_minutes=ratio_delay,
            ratio_mappings=ratio_mappings,
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
        or best.median_relative_error
        > MAX_PASS_MEDIAN_RELATIVE_ERROR
    ):
        return AlignmentResult(
            status="FAIL",
            selected_shift_minutes=None,
            archive_points=len(archive_points),
            live_points=len(live_points),
            duplicate_timestamps=0,
            scores=scores,
            ratio_delay_minutes=ratio_delay,
            ratio_mappings=ratio_mappings,
            reason="archive_oi_does_not_match_live_oi",
        )

    if len(ranked) > 1:
        second = ranked[1]
        assert second.median_relative_error is not None
        if (
            best.median_relative_error == 0
            and second.median_relative_error == 0
        ):
            clearly_better = False
        else:
            clearly_better = (
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
                ratio_delay_minutes=ratio_delay,
                ratio_mappings=ratio_mappings,
                reason="multiple_timestamp_shifts_fit_similarly",
            )

    if ratio_error is not None:
        return AlignmentResult(
            status="FAIL",
            selected_shift_minutes=None,
            archive_points=len(archive_points),
            live_points=len(live_points),
            duplicate_timestamps=0,
            scores=scores,
            ratio_delay_minutes=None,
            ratio_mappings=(),
            reason=ratio_error,
        )

    assert ratio_delay is not None
    observable_delay = max(
        0,
        best.shift_minutes,
        ratio_delay,
    )

    return AlignmentResult(
        status="PASS",
        selected_shift_minutes=observable_delay,
        archive_points=len(archive_points),
        live_points=len(live_points),
        duplicate_timestamps=0,
        scores=scores,
        ratio_delay_minutes=ratio_delay,
        ratio_mappings=ratio_mappings,
        reason="unique_oi_alignment_and_ratio_observability",
    )



def load_ratio_archive_rows(
    *,
    input_root: Path,
    symbol: str,
    start_day: date,
    end_day: date,
) -> list[ArchiveRow]:
    rows: list[ArchiveRow] = []
    current = start_day
    symbol = symbol.upper()

    while current <= end_day:
        stamp = current.isoformat()
        path = (
            input_root
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
        rows.extend(read_metrics_archive(path))
        current += timedelta(days=1)

    rows.sort(key=lambda item: item.create_time)
    return rows


async def load_live_metrics(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
) -> list[LiveRow]:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    start = datetime.combine(
        start_day,
        time.min,
        tzinfo=UTC,
    ) - timedelta(minutes=30)
    end = datetime.combine(
        end_day + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    ) + timedelta(minutes=30)

    try:
        async with session_factory() as session:
            statement = (
                select(ExchangeDerivativesSnapshotRow)
                .where(
                    ExchangeDerivativesSnapshotRow.exchange == "binance",
                    ExchangeDerivativesSnapshotRow.symbol == symbol.upper(),
                    ExchangeDerivativesSnapshotRow.timestamp >= start,
                    ExchangeDerivativesSnapshotRow.timestamp < end,
                )
                .order_by(ExchangeDerivativesSnapshotRow.timestamp)
            )
            rows = list((await session.scalars(statement)).all())
    finally:
        await engine.dispose()

    return [
        LiveRow(
            timestamp=row.timestamp,
            values={
                name: getattr(row, name)
                for name in (
                    "open_interest",
                    "long_short_ratio",
                    "top_trader_long_short_ratio",
                    "taker_buy_sell_ratio",
                )
                if getattr(row, name) is not None
            },
        )
        for row in rows
    ]


async def load_live_oi(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
) -> list[LiveOiPoint]:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    start = datetime.combine(
        start_day,
        time.min,
        tzinfo=UTC,
    ) - timedelta(minutes=10)
    end = datetime.combine(
        end_day + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    ) + timedelta(minutes=10)

    try:
        async with session_factory() as session:
            statement = (
                select(
                    ExchangeDerivativesSnapshotRow.timestamp,
                    ExchangeDerivativesSnapshotRow.open_interest,
                )
                .where(
                    ExchangeDerivativesSnapshotRow.exchange == "binance",
                    ExchangeDerivativesSnapshotRow.symbol
                    == symbol.upper(),
                    ExchangeDerivativesSnapshotRow.timestamp >= start,
                    ExchangeDerivativesSnapshotRow.timestamp < end,
                    ExchangeDerivativesSnapshotRow.open_interest.is_not(
                        None
                    ),
                )
                .order_by(
                    ExchangeDerivativesSnapshotRow.timestamp
                )
            )
            rows = (await session.execute(statement)).all()
            return [
                LiveOiPoint(
                    timestamp=row.timestamp,
                    open_interest=row.open_interest,
                )
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
        return {
            key: _json_safe(item)
            for key, item in value.items()
        }
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
    ratio_archive_rows = load_ratio_archive_rows(
        input_root=input_root,
        symbol=symbol,
        start_day=start_day,
        end_day=end_day,
    )
    live_metric_rows = await load_live_metrics(
        symbol=symbol,
        start_day=start_day,
        end_day=end_day,
    )
    ratio_results = ratio_lag_results(
        ratio_archive_rows,
        live_metric_rows,
    )
    result = evaluate_alignment(
        archive_points,
        live_points,
        ratio_results,
    )

    print(
        "METRICS_ALIGNMENT "
        f"status={result.status} "
        f"archive_points={result.archive_points} "
        f"live_points={result.live_points} "
        f"duplicates={result.duplicate_timestamps} "
        f"selected_shift_minutes="
        f"{result.selected_shift_minutes} "
        f"reason={result.reason}"
    )
    print(
        "METRICS_RATIO_ALIGNMENT "
        f"delay_minutes={result.ratio_delay_minutes} "
        f"mappings={len(result.ratio_mappings)}"
    )
    for mapping in result.ratio_mappings:
        print(
            "METRICS_RATIO_MAPPING "
            f"archive={mapping.archive_column} "
            f"live={mapping.live_field} "
            f"match_rate={mapping.match_rate:.3f} "
            f"median_lag_seconds={mapping.median_lag_seconds} "
            f"p90_lag_seconds={mapping.p90_lag_seconds}"
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
        output_dir = Path(
            "runtime-data/metrics-alignment"
        )
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path = output_dir / (
            f"{symbol.upper()}-{start_day.isoformat()}-"
            f"{end_day.isoformat()}.json"
        )
    else:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    payload = {
        **asdict(result),
        "symbol": symbol.upper(),
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "rules": {
            "shift_candidates_minutes": list(
                SHIFT_CANDIDATES_MINUTES
            ),
            "max_nearest_live_seconds": (
                MAX_NEAREST_LIVE_SECONDS
            ),
            "max_pass_median_relative_error": str(
                MAX_PASS_MEDIAN_RELATIVE_ERROR
            ),
            "minimum_matches": MIN_MATCHES,
            "clear_win_ratio": str(CLEAR_WIN_RATIO),
            "minimum_ratio_match_rate": MIN_RATIO_MATCH_RATE,
            "minimum_ratio_mapping_margin": MIN_RATIO_MAPPING_MARGIN,
        },
    }
    output_path.write_text(
        json.dumps(
            _json_safe(payload),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        f"METRICS_ALIGNMENT_ARTIFACT "
        f"path={output_path}"
    )

    return 0 if result.status == "PASS" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Binance Vision USD-M metrics OI timestamps "
            "against persisted live Binance OI without using "
            "strategy outcomes."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--input-root",
        default="runtime-data/backfill",
    )
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                symbol=args.symbol,
                start_day=date.fromisoformat(
                    args.start_date
                ),
                end_day=date.fromisoformat(
                    args.end_date
                ),
                input_root=Path(args.input_root),
                output_path=(
                    Path(args.output)
                    if args.output
                    else None
                ),
            )
        )
    )


if __name__ == "__main__":
    main()
