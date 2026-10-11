"""Audit frozen H5 development data without running the strategy."""

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pandas as pd

from crypto_signal_engine.examples.backfill_h5_funding_basis import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
)
from crypto_signal_engine.examples.h5_funding_basis_baseline import (
    FundingPoint,
    PricePoint,
    _paths,
    load_dtb3,
    load_funding,
    load_kline_prices,
)


EXPECTED_HOURLY_POINTS = (
    (DEVELOPMENT_END - DEVELOPMENT_START).days + 1
) * 24
EXPECTED_8H_FUNDING_POINTS = (
    (DEVELOPMENT_END - DEVELOPMENT_START).days + 1
) * 3


def _duplicates(timestamps: list[datetime]) -> int:
    counts = Counter(timestamps)
    return sum(count - 1 for count in counts.values() if count > 1)


def _hourly_coverage(
    points: list[PricePoint],
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    expected_start = datetime.combine(
        DEVELOPMENT_START,
        datetime.min.time(),
        tzinfo=UTC,
    ) + timedelta(hours=1) - timedelta(milliseconds=1)
    expected = pd.date_range(
        expected_start,
        periods=EXPECTED_HOURLY_POINTS,
        freq="h",
        tz="UTC",
    )
    actual = pd.DatetimeIndex([item.timestamp for item in points])
    return expected.difference(actual), actual.difference(expected)


def _max_contiguous_missing_hours(
    missing: pd.DatetimeIndex,
) -> int:
    if len(missing) == 0:
        return 0

    longest = 1
    current = 1
    for previous, current_ts in pairwise(missing):
        if (current_ts - previous).total_seconds() == 3600:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def _missing_ranges(
    missing: pd.DatetimeIndex,
) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    if len(missing) == 0:
        return []

    ranges: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
    start = missing[0]
    previous = missing[0]

    for current in missing[1:]:
        if (current - previous).total_seconds() != 3600:
            hours = int((previous - start).total_seconds() // 3600) + 1
            ranges.append((start, previous, hours))
            start = current
        previous = current

    hours = int((previous - start) / pd.Timedelta(hours=1)) + 1
    ranges.append((start, previous, hours))
    return ranges


def _spot_missing_is_tolerable(
    missing: pd.DatetimeIndex,
    *,
    unexpected_count: int,
) -> bool:
    missing_fraction = len(missing) / EXPECTED_HOURLY_POINTS
    return (
        missing_fraction < 0.001
        and _max_contiguous_missing_hours(missing) <= 6
        and unexpected_count == 0
    )


def _funding_gap_hours(points: list[FundingPoint]) -> list[int]:
    return [
        int(
            (
                later.timestamp - earlier.timestamp
            ).total_seconds()
            // 3600
        )
        for earlier, later in pairwise(points)
    ]


def run(*, symbol: str, input_root: Path) -> None:
    symbol = symbol.upper()
    spot = load_kline_prices(
        _paths(
            root=input_root,
            symbol=symbol,
            market="spot",
            dataset="klines",
        )
    )
    perp = load_kline_prices(
        _paths(
            root=input_root,
            symbol=symbol,
            market="futures/um",
            dataset="klines",
        )
    )
    funding = load_funding(
        _paths(
            root=input_root,
            symbol=symbol,
            market="futures/um",
            dataset="fundingRate",
        )
    )
    dtb3 = load_dtb3(
        input_root
        / symbol
        / "funding-basis/benchmark/DTB3.csv"
    )

    spot_duplicates = _duplicates(
        [item.timestamp for item in spot]
    )
    perp_duplicates = _duplicates(
        [item.timestamp for item in perp]
    )
    funding_duplicates = _duplicates(
        [item.timestamp for item in funding]
    )
    spot_missing, spot_unexpected = _hourly_coverage(spot)
    perp_missing, perp_unexpected = _hourly_coverage(perp)
    spot_max_gap = _max_contiguous_missing_hours(spot_missing)
    perp_max_gap = _max_contiguous_missing_hours(perp_missing)

    gaps = _funding_gap_hours(funding)
    gap_counts = Counter(gaps)
    max_funding_gap = max(gaps) if gaps else 0

    dtb3_first = dtb3.index.min()
    dtb3_last = dtb3.index.max()
    dtb3_seed_ok = (
        dtb3_first.date() <= DEVELOPMENT_START
        and dtb3_last.date() >= DEVELOPMENT_END - timedelta(days=7)
    )

    spot_ok = (
        spot_duplicates == 0
        and _spot_missing_is_tolerable(
            spot_missing,
            unexpected_count=len(spot_unexpected),
        )
    )
    perp_ok = (
        len(perp) == EXPECTED_HOURLY_POINTS
        and perp_duplicates == 0
        and len(perp_missing) == 0
        and len(perp_unexpected) == 0
    )
    funding_ok = (
        len(funding) > 0
        and funding_duplicates == 0
        and funding[0].timestamp.date() == DEVELOPMENT_START
        and funding[-1].timestamp.date() == DEVELOPMENT_END
        and max_funding_gap <= 24
    )
    status = (
        "PASS"
        if spot_ok and perp_ok and funding_ok and dtb3_seed_ok
        else "FAIL"
    )

    print(
        "H5_DATA_AUDIT "
        f"status={status} "
        f"symbol={symbol} "
        f"start={DEVELOPMENT_START.isoformat()} "
        f"end={DEVELOPMENT_END.isoformat()}"
    )
    print(
        "H5_DATA_SPOT "
        f"points={len(spot)} "
        f"expected={EXPECTED_HOURLY_POINTS} "
        f"missing_hours={len(spot_missing)} "
        f"unexpected_timestamps={len(spot_unexpected)} "
        f"max_contiguous_missing_hours={spot_max_gap} "
        f"missing_fraction={len(spot_missing) / EXPECTED_HOURLY_POINTS:.6f} "
        f"tolerable={str(_spot_missing_is_tolerable(spot_missing, unexpected_count=len(spot_unexpected))).lower()} "
        f"duplicates={spot_duplicates} "
        f"first={spot[0].timestamp.isoformat()} "
        f"last={spot[-1].timestamp.isoformat()}"
    )
    print(
        "H5_DATA_PERP "
        f"points={len(perp)} "
        f"expected={EXPECTED_HOURLY_POINTS} "
        f"missing_hours={len(perp_missing)} "
        f"unexpected_timestamps={len(perp_unexpected)} "
        f"max_contiguous_missing_hours={perp_max_gap} "
        f"duplicates={perp_duplicates} "
        f"first={perp[0].timestamp.isoformat()} "
        f"last={perp[-1].timestamp.isoformat()}"
    )
    if len(spot_missing):
        print(
            "H5_DATA_SPOT_MISSING "
            + ",".join(item.isoformat() for item in spot_missing)
        )
    if len(spot_unexpected):
        print(
            "H5_DATA_SPOT_UNEXPECTED "
            + ",".join(item.isoformat() for item in spot_unexpected)
        )
    if len(perp_missing):
        print(
            "H5_DATA_PERP_MISSING "
            + ",".join(item.isoformat() for item in perp_missing)
        )
    if len(perp_unexpected):
        print(
            "H5_DATA_PERP_UNEXPECTED "
            + ",".join(item.isoformat() for item in perp_unexpected)
        )

    spot_ranges = sorted(
        _missing_ranges(spot_missing),
        key=lambda item: (-item[2], item[0]),
    )
    for rank, (start, end, hours) in enumerate(
        spot_ranges[:10],
        start=1,
    ):
        print(
            "H5_DATA_SPOT_GAP "
            f"rank={rank} "
            f"hours={hours} "
            f"start={start.isoformat()} "
            f"end={end.isoformat()}"
        )

    print(
        "H5_DATA_FUNDING "
        f"points={len(funding)} "
        f"expected_if_8h={EXPECTED_8H_FUNDING_POINTS} "
        f"duplicates={funding_duplicates} "
        f"first={funding[0].timestamp.isoformat()} "
        f"last={funding[-1].timestamp.isoformat()} "
        f"max_gap_hours={max_funding_gap}"
    )
    print(
        "H5_DATA_FUNDING_INTERVALS "
        + " ".join(
            f"{hours}h={count}"
            for hours, count in sorted(gap_counts.items())
        )
    )
    print(
        "H5_DATA_DTB3 "
        f"observations={len(dtb3)} "
        f"first={dtb3_first.isoformat()} "
        f"last={dtb3_last.isoformat()} "
        f"seed_ok={str(dtb3_seed_ok).lower()}"
    )

    if status != "PASS":
        raise RuntimeError(
            "H5 development data audit failed; do not run the baseline."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit H5 development inputs without strategy evaluation."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--input-root",
        default="runtime-data/h5",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        symbol=args.symbol,
        input_root=Path(args.input_root),
    )


if __name__ == "__main__":
    main()
