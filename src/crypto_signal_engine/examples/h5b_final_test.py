"""Run the frozen H5b 2024-2025 final test.

This command is intentionally fixed to the untouched 2024-01-01 through
2025-12-31 final-test period. Do not change its rules after primary result
metrics have been produced.
"""

import argparse
import csv
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path

import pandas as pd

from crypto_signal_engine.examples.h5_funding_basis_baseline import (
    FundingPoint,
    PricePoint,
    SENSITIVITY_ROUND_TRIP_PCT,
    _cost_model_from_round_trip_pct,
    _normalized_header,
    _parse_archive_timestamp,
    detailed_cost_model,
)
from crypto_signal_engine.examples.h5b_two_sided_carry import (
    H5bSimulationResult,
    simulate_h5b,
)


FINAL_START = date(2024, 1, 1)
FINAL_END = date(2025, 12, 31)
FRED_WARMUP_START = date(2023, 12, 1)
EXPECTED_HOURLY_POINTS = 17_544


@dataclass(frozen=True, slots=True)
class FinalMonth:
    year: int
    month: int

    @property
    def stamp(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def final_month_range() -> tuple[FinalMonth, ...]:
    output: list[FinalMonth] = []
    year = FINAL_START.year
    month = FINAL_START.month
    while (year, month) <= (FINAL_END.year, FINAL_END.month):
        output.append(FinalMonth(year=year, month=month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return tuple(output)


def _assert_final_timestamp(
    timestamp: datetime,
    *,
    source: str,
) -> None:
    day = timestamp.astimezone(UTC).date()
    if day < FINAL_START or day > FINAL_END:
        raise RuntimeError(
            "H5b final runner refuses non-final-test data: "
            f"{source} contains {timestamp.isoformat()}"
        )


def _zip_csv_rows(path: Path) -> list[list[str]]:
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
                return list(csv.reader(text))


def load_final_kline_prices(
    paths: list[Path],
) -> list[PricePoint]:
    output: list[PricePoint] = []
    for path in paths:
        for row in _zip_csv_rows(path):
            if not row:
                continue
            if not row[0].strip().isdigit():
                continue
            if len(row) < 7:
                raise ValueError(f"Unexpected kline row in {path}: {row}")

            open_timestamp = _parse_archive_timestamp(
                row[0].strip()
            )
            timestamp = (
                open_timestamp
                + timedelta(hours=1)
                - timedelta(milliseconds=1)
            )
            _assert_final_timestamp(timestamp, source=str(path))
            output.append(
                PricePoint(
                    timestamp=timestamp,
                    close=Decimal(row[4].strip()),
                )
            )

    output.sort(key=lambda item: item.timestamp)
    if not output:
        raise ValueError("No H5b final kline prices loaded")
    return output


def load_final_funding(
    paths: list[Path],
) -> list[FundingPoint]:
    output: list[FundingPoint] = []

    for path in paths:
        rows = _zip_csv_rows(path)
        if not rows:
            continue

        header = _normalized_header(rows[0])
        has_header = any(
            not value.lstrip("-").replace(".", "", 1).isdigit()
            for value in rows[0]
        )
        data_rows = rows[1:] if has_header else rows

        if has_header:
            index = {name: i for i, name in enumerate(header)}
            time_index = next(
                (
                    index[name]
                    for name in (
                        "calc_time",
                        "funding_time",
                        "timestamp",
                        "time",
                    )
                    if name in index
                ),
                None,
            )
            rate_index = next(
                (
                    index[name]
                    for name in (
                        "last_funding_rate",
                        "funding_rate",
                        "rate",
                    )
                    if name in index
                ),
                None,
            )
            interval_index = next(
                (
                    index[name]
                    for name in (
                        "funding_interval_hours",
                        "interval_hours",
                    )
                    if name in index
                ),
                None,
            )
            if time_index is None or rate_index is None:
                raise ValueError(
                    f"Unsupported funding header in {path}: {header}"
                )
        else:
            if len(rows[0]) < 2:
                raise ValueError(
                    f"Unsupported funding row in {path}: {rows[0]}"
                )
            time_index = 0
            interval_index = 1 if len(rows[0]) >= 3 else None
            rate_index = 2 if len(rows[0]) >= 3 else 1

        for row in data_rows:
            if not row:
                continue
            timestamp = _parse_archive_timestamp(
                row[time_index].strip()
            )
            _assert_final_timestamp(timestamp, source=str(path))
            interval = (
                Decimal(row[interval_index].strip())
                if interval_index is not None
                and row[interval_index].strip()
                else None
            )
            output.append(
                FundingPoint(
                    timestamp=timestamp,
                    funding_rate=Decimal(row[rate_index].strip()),
                    interval_hours=interval,
                )
            )

    output.sort(key=lambda item: item.timestamp)
    if not output:
        raise ValueError("No H5b final funding rows loaded")
    return output


def load_final_dtb3(path: Path) -> pd.Series:
    frame = pd.read_csv(path)
    date_column = (
        "DATE"
        if "DATE" in frame.columns
        else "observation_date"
    )
    if date_column not in frame.columns or "DTB3" not in frame.columns:
        raise ValueError(
            f"Unexpected DTB3 columns: {list(frame.columns)}"
        )

    frame[date_column] = pd.to_datetime(
        frame[date_column],
        utc=True,
    )
    frame["DTB3"] = pd.to_numeric(
        frame["DTB3"],
        errors="coerce",
    )
    frame = frame.dropna(subset=["DTB3"]).sort_values(date_column)

    forbidden = frame[
        frame[date_column].dt.date > FINAL_END
    ]
    if not forbidden.empty:
        raise RuntimeError(
            "H5b final runner refuses DTB3 observations after "
            f"{FINAL_END.isoformat()}."
        )

    return frame.set_index(date_column)["DTB3"]


def benchmark_returns_final(
    dtb3: pd.Series,
) -> tuple[Decimal, Decimal, dict[int, Decimal]]:
    days = pd.date_range(
        FINAL_START,
        FINAL_END,
        freq="D",
        tz="UTC",
    )
    expanded = (
        dtb3.reindex(dtb3.index.union(days))
        .sort_index()
        .ffill()
        .reindex(days)
    )
    if expanded.isna().any():
        raise ValueError(
            "DTB3 has no prior observation to seed H5b final test"
        )

    daily_factors = 1.0 + (expanded / 100.0 / 360.0)
    cumulative = float(daily_factors.prod() - 1.0)
    years = (FINAL_END - FINAL_START).days / 365.2425
    annualized = (1.0 + cumulative) ** (1.0 / years) - 1.0

    yearly: dict[int, Decimal] = {}
    for year, values in daily_factors.groupby(
        daily_factors.index.year
    ):
        yearly[int(year)] = Decimal(
            str((float(values.prod()) - 1.0) * 100.0)
        )

    return (
        Decimal(str(cumulative * 100.0)),
        Decimal(str(annualized * 100.0)),
        yearly,
    )


def final_paths(
    *,
    root: Path,
    symbol: str,
    market: str,
    dataset: str,
) -> list[Path]:
    base = root / symbol.upper() / "funding-basis"
    output: list[Path] = []

    for month in final_month_range():
        stamp = month.stamp
        if dataset == "fundingRate":
            path = (
                base
                / "futures/um/fundingRate"
                / f"{symbol.upper()}-fundingRate-{stamp}.zip"
            )
        else:
            path = (
                base
                / market
                / "klines/1h"
                / f"{symbol.upper()}-1h-{stamp}.zip"
            )
        if not path.exists():
            raise FileNotFoundError(f"Missing H5b final input: {path}")
        output.append(path)

    return output


def evaluate_primary_gate(
    result: H5bSimulationResult,
    *,
    benchmark_annualized_pct: Decimal,
    benchmark_yearly_pct: dict[int, Decimal],
) -> tuple[bool, Decimal, dict[int, Decimal]]:
    yearly_excess = {
        year: result.yearly_returns_pct[year]
        - benchmark_yearly_pct[year]
        for year in (2024, 2025)
    }
    annualized_excess = (
        result.annualized_return_pct
        - benchmark_annualized_pct
    )
    passes = (
        result.net_return_pct > 0
        and yearly_excess[2024] > 0
        and yearly_excess[2025] > 0
        and annualized_excess >= Decimal("3")
        and result.static_margin_stress_survives
    )
    return passes, annualized_excess, yearly_excess


def print_h5b_result(
    result: H5bSimulationResult,
    *,
    benchmark_annualized_pct: Decimal,
    benchmark_yearly_pct: dict[int, Decimal],
    primary: bool,
) -> None:
    excess = (
        result.annualized_return_pct
        - benchmark_annualized_pct
    )
    print(
        "H5B_RESULT "
        f"leverage={result.leverage}x "
        f"cost_model={result.cost_model} "
        f"net_return={result.net_return_pct:.4f}% "
        f"annualized={result.annualized_return_pct:.4f}% "
        f"benchmark_annualized={benchmark_annualized_pct:.4f}% "
        f"excess_annualized={excess:.4f}pp "
        f"funding_income={result.funding_income:.2f} "
        f"trading_costs={result.trading_costs:.2f} "
        f"max_drawdown={result.max_drawdown_pct:.4f}% "
        f"down_resizes={result.down_resizes} "
        f"up_resizes={result.up_resizes} "
        f"resized_btc={result.resized_btc:.8f} "
        f"minimum_margin_equity={result.minimum_margin_equity:.2f} "
        f"worst_24h_perp_rise={result.worst_24h_perp_rise_pct:.4f}% "
        "static_margin_stress_survives="
        f"{str(result.static_margin_stress_survives).lower()}"
    )

    for year in (2024, 2025):
        strategy = result.yearly_returns_pct[year]
        benchmark = benchmark_yearly_pct[year]
        print(
            "H5B_YEAR "
            f"leverage={result.leverage}x "
            f"cost_model={result.cost_model} "
            f"year={year} "
            f"strategy={strategy:.4f}% "
            f"benchmark={benchmark:.4f}% "
            f"excess={strategy - benchmark:+.4f}pp"
        )

    if primary:
        passes, annualized_excess, yearly_excess = (
            evaluate_primary_gate(
                result,
                benchmark_annualized_pct=benchmark_annualized_pct,
                benchmark_yearly_pct=benchmark_yearly_pct,
            )
        )
        print(
            "H5B_FINAL_GATE "
            f"status={'PASS' if passes else 'FAIL'} "
            f"net_positive={str(result.net_return_pct > 0).lower()} "
            f"excess_2024={yearly_excess[2024]:+.4f}pp "
            f"excess_2025={yearly_excess[2025]:+.4f}pp "
            f"excess_annualized={annualized_excess:+.4f}pp "
            "stress_survives="
            f"{str(result.static_margin_stress_survives).lower()}"
        )


def require_final_audit_pass(
    *,
    input_root: Path,
    symbol: str,
) -> None:
    path = (
        input_root
        / symbol.upper()
        / "h5b-final-audit.json"
    )
    if not path.exists():
        raise RuntimeError(
            "H5b final test requires a PASS data-audit artifact. "
            "Run h5b-final-audit first."
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "status": "PASS",
        "symbol": symbol.upper(),
        "start": FINAL_START.isoformat(),
        "end": FINAL_END.isoformat(),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(
                "H5b final test refuses invalid data-audit artifact: "
                f"{key}={payload.get(key)!r}, expected={value!r}"
            )


def run(
    *,
    symbol: str,
    input_root: Path,
) -> None:
    symbol = symbol.upper()
    require_final_audit_pass(
        input_root=input_root,
        symbol=symbol,
    )
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
    benchmark_total, benchmark_annualized, benchmark_yearly = (
        benchmark_returns_final(dtb3)
    )

    print(
        "H5B_BENCHMARK "
        "series=DTB3 "
        "conversion=simple_daily_rate_over_360 "
        f"cumulative={benchmark_total:.4f}% "
        f"annualized={benchmark_annualized:.4f}%"
    )
    print(
        "H5B_INPUT "
        f"symbol={symbol} "
        f"start={FINAL_START.isoformat()} "
        f"end={FINAL_END.isoformat()} "
        f"spot_points={len(spot)} "
        f"perp_points={len(perp)} "
        f"funding_points={len(funding)}"
    )

    models = [detailed_cost_model()]
    models.extend(
        _cost_model_from_round_trip_pct(pct)
        for pct in SENSITIVITY_ROUND_TRIP_PCT
    )

    for leverage in (Decimal("1"), Decimal("2")):
        for model in models:
            result = simulate_h5b(
                leverage=leverage,
                spot_prices=spot,
                perp_prices=perp,
                funding=funding,
                cost_model=model,
            )
            primary = (
                leverage == Decimal("1")
                and model.name == "detailed_0.38pct"
            )
            print_h5b_result(
                result,
                benchmark_annualized_pct=benchmark_annualized,
                benchmark_yearly_pct=benchmark_yearly,
                primary=primary,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen H5b 2024-2025 final test exactly once."
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
