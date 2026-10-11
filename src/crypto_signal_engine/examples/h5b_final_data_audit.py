"""Audit H5b final-test inputs without running the strategy."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from crypto_signal_engine.examples.h5_data_audit import (
    _duplicates,
    _funding_gap_hours,
    _max_contiguous_missing_hours,
    _missing_ranges,
)
from crypto_signal_engine.examples.h5b_final_test import (
    EXPECTED_HOURLY_POINTS,
    FINAL_END,
    FINAL_START,
    final_paths,
    load_final_dtb3,
    load_final_funding,
    load_final_kline_prices,
)


EXPECTED_8H_FUNDING_POINTS = (
    (FINAL_END - FINAL_START).days + 1
) * 3
MAX_MISSING_SPOT_HOURS = 17
MAX_CONTIGUOUS_SPOT_GAP_HOURS = 6


def _hourly_coverage(
    timestamps: list[datetime],
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    expected_start = datetime.combine(
        FINAL_START,
        datetime.min.time(),
        tzinfo=UTC,
    ) + timedelta(hours=1) - timedelta(milliseconds=1)
    expected = pd.date_range(
        expected_start,
        periods=EXPECTED_HOURLY_POINTS,
        freq="h",
        tz="UTC",
    )
    actual = pd.DatetimeIndex(timestamps)
    return expected.difference(actual), actual.difference(expected)


def _spot_is_tolerable(
    missing: pd.DatetimeIndex,
    *,
    unexpected_count: int,
    duplicates: int,
) -> bool:
    return (
        len(missing) <= MAX_MISSING_SPOT_HOURS
        and _max_contiguous_missing_hours(missing)
        <= MAX_CONTIGUOUS_SPOT_GAP_HOURS
        and unexpected_count == 0
        and duplicates == 0
    )


def audit_artifact_path(
    *,
    input_root: Path,
    symbol: str,
) -> Path:
    return (
        input_root
        / symbol.upper()
        / "h5b-final-audit.json"
    )


def run(
    *,
    symbol: str,
    input_root: Path,
) -> None:
    symbol = symbol.upper()

    spot = load_final_kline_prices(
        final_paths(
            root=input_root,
            symbol=symbol,
            market="spot",
            dataset="klines",
        )
    )
    perp = load_final_kline_prices(
        final_paths(
            root=input_root,
            symbol=symbol,
            market="futures/um",
            dataset="klines",
        )
    )
    funding = load_final_funding(
        final_paths(
            root=input_root,
            symbol=symbol,
            market="futures/um",
            dataset="fundingRate",
        )
    )
    dtb3 = load_final_dtb3(
        input_root
        / symbol
        / "funding-basis/benchmark/DTB3.csv"
    )

    spot_timestamps = [item.timestamp for item in spot]
    perp_timestamps = [item.timestamp for item in perp]
    funding_timestamps = [item.timestamp for item in funding]

    spot_duplicates = _duplicates(spot_timestamps)
    perp_duplicates = _duplicates(perp_timestamps)
    funding_duplicates = _duplicates(funding_timestamps)

    spot_missing, spot_unexpected = _hourly_coverage(
        spot_timestamps
    )
    perp_missing, perp_unexpected = _hourly_coverage(
        perp_timestamps
    )
    spot_max_gap = _max_contiguous_missing_hours(spot_missing)
    perp_max_gap = _max_contiguous_missing_hours(perp_missing)

    funding_gaps = _funding_gap_hours(funding)
    funding_gap_counts = Counter(funding_gaps)
    max_funding_gap = (
        max(funding_gaps)
        if funding_gaps
        else 0
    )

    dtb3_first = dtb3.index.min()
    dtb3_last = dtb3.index.max()
    dtb3_seed_ok = (
        dtb3_first.date() <= FINAL_START
        and dtb3_last.date()
        >= FINAL_END - timedelta(days=7)
    )

    spot_ok = _spot_is_tolerable(
        spot_missing,
        unexpected_count=len(spot_unexpected),
        duplicates=spot_duplicates,
    )
    perp_ok = (
        len(perp) == EXPECTED_HOURLY_POINTS
        and perp_duplicates == 0
        and len(perp_missing) == 0
        and len(perp_unexpected) == 0
    )
    funding_ok = (
        bool(funding)
        and funding_duplicates == 0
        and funding[0].timestamp.date() == FINAL_START
        and funding[-1].timestamp.date() == FINAL_END
        and max_funding_gap <= 8
    )
    status = (
        "PASS"
        if spot_ok and perp_ok and funding_ok and dtb3_seed_ok
        else "FAIL"
    )

    print(
        "H5B_DATA_AUDIT "
        f"status={status} "
        f"symbol={symbol} "
        f"start={FINAL_START.isoformat()} "
        f"end={FINAL_END.isoformat()}"
    )
    print(
        "H5B_DATA_SPOT "
        f"points={len(spot)} "
        f"expected={EXPECTED_HOURLY_POINTS} "
        f"missing_hours={len(spot_missing)} "
        f"allowed_missing_hours={MAX_MISSING_SPOT_HOURS} "
        f"unexpected_timestamps={len(spot_unexpected)} "
        f"max_contiguous_missing_hours={spot_max_gap} "
        f"allowed_max_gap_hours={MAX_CONTIGUOUS_SPOT_GAP_HOURS} "
        f"duplicates={spot_duplicates} "
        f"tolerable={str(spot_ok).lower()} "
        f"first={spot[0].timestamp.isoformat()} "
        f"last={spot[-1].timestamp.isoformat()}"
    )
    print(
        "H5B_DATA_PERP "
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
            "H5B_DATA_SPOT_MISSING "
            + ",".join(
                item.isoformat()
                for item in spot_missing
            )
        )
    if len(spot_unexpected):
        print(
            "H5B_DATA_SPOT_UNEXPECTED "
            + ",".join(
                item.isoformat()
                for item in spot_unexpected
            )
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
            "H5B_DATA_SPOT_GAP "
            f"rank={rank} "
            f"hours={hours} "
            f"start={start.isoformat()} "
            f"end={end.isoformat()}"
        )

    print(
        "H5B_DATA_FUNDING "
        f"points={len(funding)} "
        f"expected_if_8h={EXPECTED_8H_FUNDING_POINTS} "
        f"duplicates={funding_duplicates} "
        f"first={funding[0].timestamp.isoformat()} "
        f"last={funding[-1].timestamp.isoformat()} "
        f"max_gap_hours={max_funding_gap}"
    )
    print(
        "H5B_DATA_FUNDING_INTERVALS "
        + " ".join(
            f"{hours}h={count}"
            for hours, count in sorted(
                funding_gap_counts.items()
            )
        )
    )
    print(
        "H5B_DATA_DTB3 "
        f"observations={len(dtb3)} "
        f"first={dtb3_first.isoformat()} "
        f"last={dtb3_last.isoformat()} "
        f"seed_ok={str(dtb3_seed_ok).lower()}"
    )

    artifact = audit_artifact_path(
        input_root=input_root,
        symbol=symbol,
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "status": status,
                "symbol": symbol,
                "start": FINAL_START.isoformat(),
                "end": FINAL_END.isoformat(),
                "spot_points": len(spot),
                "spot_missing_hours": len(spot_missing),
                "spot_unexpected_timestamps": len(
                    spot_unexpected
                ),
                "spot_max_contiguous_missing_hours": (
                    spot_max_gap
                ),
                "perp_points": len(perp),
                "perp_missing_hours": len(perp_missing),
                "funding_points": len(funding),
                "funding_max_gap_hours": max_funding_gap,
                "dtb3_seed_ok": dtb3_seed_ok,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "H5B_DATA_AUDIT_ARTIFACT "
        f"path={artifact}"
    )

    if status != "PASS":
        raise RuntimeError(
            "H5b final-test data audit failed; "
            "do not run the final test."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit H5b 2024-2025 final-test inputs "
            "without evaluating strategy returns."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--input-root",
        default="runtime-data/h5b-final",
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
