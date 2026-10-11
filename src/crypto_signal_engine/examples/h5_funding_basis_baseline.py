"""Run the frozen H5 always-on spot-long/perpetual-short baseline.

Development-only: 2020-01-01 through 2023-12-31. This module refuses
input rows outside the H5 development window and never reads the frozen
2024-2025 final-test period.
"""

import argparse
import csv
import math
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path

import pandas as pd

from crypto_signal_engine.examples.backfill_h5_funding_basis import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    month_range,
)


INITIAL_SPOT_NOTIONAL = Decimal("10000")
REBALANCE_TRIGGER_FRACTION = Decimal("0.50")
SPOT_FEE_RATE = Decimal("0.0010")
FUTURES_FEE_RATE = Decimal("0.0005")
SLIPPAGE_RATE = Decimal("0.0002")
DETAILED_SPOT_EXECUTION_COST = SPOT_FEE_RATE + SLIPPAGE_RATE
DETAILED_FUTURES_EXECUTION_COST = FUTURES_FEE_RATE + SLIPPAGE_RATE
SENSITIVITY_ROUND_TRIP_PCT = (
    Decimal("0.20"),
    Decimal("0.30"),
    Decimal("0.40"),
)


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    close: Decimal


@dataclass(frozen=True, slots=True)
class FundingPoint:
    timestamp: datetime
    funding_rate: Decimal
    interval_hours: Decimal | None


@dataclass(frozen=True, slots=True)
class CostModel:
    name: str
    spot_execution_rate: Decimal
    futures_execution_rate: Decimal


@dataclass(frozen=True, slots=True)
class SimulationResult:
    leverage: Decimal
    cost_model: str
    starting_capital: Decimal
    ending_equity: Decimal
    net_return_pct: Decimal
    annualized_return_pct: Decimal
    funding_income: Decimal
    trading_costs: Decimal
    rebalances: int
    rebalanced_btc: Decimal
    transferred_to_margin: Decimal
    max_drawdown_pct: Decimal
    negative_funding_fraction: Decimal
    longest_negative_funding_streak_hours: Decimal
    worst_24h_perp_rise_pct: Decimal
    static_margin_stress_survives: bool
    yearly_returns_pct: dict[int, Decimal]
    minimum_margin_equity: Decimal


def _parse_archive_timestamp(value: str) -> datetime:
    raw = int(value)
    divisor = Decimal("1000000") if raw >= 100_000_000_000_000 else Decimal("1000")
    return datetime.fromtimestamp(
        float(Decimal(raw) / divisor),
        tz=UTC,
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


def _assert_development_timestamp(timestamp: datetime, *, source: str) -> None:
    day = timestamp.astimezone(UTC).date()
    if day < DEVELOPMENT_START or day > DEVELOPMENT_END:
        raise RuntimeError(
            "H5 calculator refuses non-development data: "
            f"{source} contains {timestamp.isoformat()}"
        )


def load_kline_prices(
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
            timestamp = _parse_archive_timestamp(row[6].strip())
            _assert_development_timestamp(timestamp, source=str(path))
            output.append(
                PricePoint(
                    timestamp=timestamp,
                    close=Decimal(row[4].strip()),
                )
            )
    output.sort(key=lambda item: item.timestamp)
    if not output:
        raise ValueError("No kline prices loaded")
    return output


def _normalized_header(row: list[str]) -> tuple[str, ...]:
    return tuple(
        value.strip().lower().replace(" ", "_").replace("-", "_")
        for value in row
    )


def load_funding(
    paths: list[Path],
) -> list[FundingPoint]:
    output: list[FundingPoint] = []
    for path in paths:
        rows = _zip_csv_rows(path)
        if not rows:
            continue

        header = _normalized_header(rows[0])
        has_header = any(not value.lstrip("-").replace(".", "", 1).isdigit() for value in rows[0])
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
                raise ValueError(f"Unsupported funding row in {path}: {rows[0]}")
            time_index = 0
            interval_index = 1 if len(rows[0]) >= 3 else None
            rate_index = 2 if len(rows[0]) >= 3 else 1

        for row in data_rows:
            if not row:
                continue
            timestamp = _parse_archive_timestamp(row[time_index].strip())
            _assert_development_timestamp(timestamp, source=str(path))
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
        raise ValueError("No funding rows loaded")
    return output


def _latest_price_at_or_before(
    points: list[PricePoint],
    timestamp: datetime,
    *,
    cursor: int,
) -> tuple[PricePoint, int]:
    while (
        cursor + 1 < len(points)
        and points[cursor + 1].timestamp <= timestamp
    ):
        cursor += 1
    if points[cursor].timestamp > timestamp:
        raise ValueError(
            f"No observable price at or before {timestamp.isoformat()}"
        )
    return points[cursor], cursor


def load_dtb3(path: Path) -> pd.Series:
    frame = pd.read_csv(path)
    date_column = "DATE" if "DATE" in frame.columns else "observation_date"
    if date_column not in frame.columns or "DTB3" not in frame.columns:
        raise ValueError(
            f"Unexpected DTB3 columns: {list(frame.columns)}"
        )

    frame[date_column] = pd.to_datetime(frame[date_column], utc=True)
    frame["DTB3"] = pd.to_numeric(frame["DTB3"], errors="coerce")
    frame = frame.dropna(subset=["DTB3"]).sort_values(date_column)

    forbidden = frame[
        frame[date_column].dt.date > DEVELOPMENT_END
    ]
    if not forbidden.empty:
        raise RuntimeError(
            "H5 calculator refuses DTB3 observations after "
            f"{DEVELOPMENT_END.isoformat()}."
        )

    return frame.set_index(date_column)["DTB3"]


def benchmark_returns(
    dtb3: pd.Series,
) -> tuple[Decimal, Decimal, dict[int, Decimal]]:
    days = pd.date_range(
        DEVELOPMENT_START,
        DEVELOPMENT_END,
        freq="D",
        tz="UTC",
    )
    expanded = dtb3.reindex(dtb3.index.union(days)).sort_index().ffill()
    expanded = expanded.reindex(days)
    if expanded.isna().any():
        raise ValueError(
            "DTB3 has no prior observation to seed the development period"
        )

    daily_factors = 1.0 + (expanded / 100.0 / 360.0)
    cumulative = float(daily_factors.prod() - 1.0)
    years = (DEVELOPMENT_END - DEVELOPMENT_START).days / 365.2425
    annualized = (1.0 + cumulative) ** (1.0 / years) - 1.0

    yearly: dict[int, Decimal] = {}
    for year, values in daily_factors.groupby(daily_factors.index.year):
        yearly[int(year)] = Decimal(
            str((float(values.prod()) - 1.0) * 100.0)
        )

    return (
        Decimal(str(cumulative * 100.0)),
        Decimal(str(annualized * 100.0)),
        yearly,
    )


def _annualized_return(
    *,
    starting: Decimal,
    ending: Decimal,
    start: datetime,
    end: datetime,
) -> Decimal:
    if starting <= 0 or ending <= 0:
        return Decimal("-100")
    years = Decimal(str((end - start).total_seconds())) / Decimal(
        str(365.2425 * 24 * 3600)
    )
    if years <= 0:
        return Decimal("0")
    value = math.pow(
        float(ending / starting),
        1.0 / float(years),
    ) - 1.0
    return Decimal(str(value * 100.0))


def _cost_model_from_round_trip_pct(
    pct: Decimal,
) -> CostModel:
    total_rate = pct / Decimal("100")
    # Preserve the frozen 2:1 spot:futures fee relationship.
    spot = total_rate / Decimal("3")
    futures = total_rate / Decimal("6")
    return CostModel(
        name=f"sensitivity_{pct}pct",
        spot_execution_rate=spot,
        futures_execution_rate=futures,
    )


def detailed_cost_model() -> CostModel:
    return CostModel(
        name="detailed_0.38pct",
        spot_execution_rate=DETAILED_SPOT_EXECUTION_COST,
        futures_execution_rate=DETAILED_FUTURES_EXECUTION_COST,
    )


def worst_24h_perp_rise_pct(
    prices: list[PricePoint],
) -> Decimal:
    series = pd.Series(
        [float(item.close) for item in prices],
        index=pd.DatetimeIndex([item.timestamp for item in prices]),
    )
    prior = series.reindex(
        series.index - pd.Timedelta(hours=24),
        method="pad",
    )
    prior.index = series.index
    rises = (series / prior - 1.0) * 100.0
    return Decimal(str(float(rises.max())))


def _longest_negative_streak_hours(
    funding: list[FundingPoint],
) -> Decimal:
    longest = Decimal("0")
    current = Decimal("0")
    previous: datetime | None = None

    for item in funding:
        if item.funding_rate < 0:
            interval = item.interval_hours
            if interval is None:
                if previous is None:
                    interval = Decimal("0")
                else:
                    interval = Decimal(
                        str(
                            (
                                item.timestamp - previous
                            ).total_seconds()
                            / 3600.0
                        )
                    )
            current += interval
            longest = max(longest, current)
        else:
            current = Decimal("0")
        previous = item.timestamp

    return longest


def simulate(
    *,
    leverage: Decimal,
    spot_prices: list[PricePoint],
    perp_prices: list[PricePoint],
    funding: list[FundingPoint],
    cost_model: CostModel,
) -> SimulationResult:
    if leverage not in (Decimal("1"), Decimal("2")):
        raise ValueError("H5 baseline supports only 1x or 2x leverage")

    first_time = max(
        spot_prices[0].timestamp,
        perp_prices[0].timestamp,
        funding[0].timestamp,
        datetime.combine(DEVELOPMENT_START, datetime.min.time(), tzinfo=UTC),
    )
    end_time = min(
        spot_prices[-1].timestamp,
        perp_prices[-1].timestamp,
        datetime.combine(
            DEVELOPMENT_END,
            datetime.max.time(),
            tzinfo=UTC,
        ),
    )

    spot_cursor = 0
    perp_cursor = 0
    spot_entry, spot_cursor = _latest_price_at_or_before(
        spot_prices,
        first_time,
        cursor=spot_cursor,
    )
    perp_entry, perp_cursor = _latest_price_at_or_before(
        perp_prices,
        first_time,
        cursor=perp_cursor,
    )

    quantity = INITIAL_SPOT_NOTIONAL / spot_entry.close
    short_entry_price = perp_entry.close
    initial_short_notional = quantity * short_entry_price
    initial_margin = initial_short_notional / leverage
    starting_capital = INITIAL_SPOT_NOTIONAL + initial_margin

    entry_cost = (
        INITIAL_SPOT_NOTIONAL * cost_model.spot_execution_rate
        + initial_short_notional * cost_model.futures_execution_rate
    )
    margin_cash = initial_margin - entry_cost
    trading_costs = entry_cost
    funding_income = Decimal("0")
    rebalances = 0
    rebalanced_btc = Decimal("0")
    transferred_to_margin = Decimal("0")
    minimum_margin_equity = margin_cash

    equity_peak = starting_capital
    max_drawdown = Decimal("0")
    yearly_start_equity: dict[int, Decimal] = {}
    yearly_end_equity: dict[int, Decimal] = {}

    funding_by_time: dict[datetime, list[FundingPoint]] = {}
    for item in funding:
        if first_time <= item.timestamp <= end_time:
            funding_by_time.setdefault(item.timestamp, []).append(item)

    timeline = sorted(
        {
            point.timestamp
            for point in perp_prices
            if first_time <= point.timestamp <= end_time
        }
        | set(funding_by_time)
    )

    def portfolio_equity(
        spot_price: Decimal,
        perp_price: Decimal,
    ) -> Decimal:
        futures_unrealized = quantity * (
            short_entry_price - perp_price
        )
        return (
            quantity * spot_price
            + margin_cash
            + futures_unrealized
        )

    for timestamp in timeline:
        spot_point, spot_cursor = _latest_price_at_or_before(
            spot_prices,
            timestamp,
            cursor=spot_cursor,
        )
        perp_point, perp_cursor = _latest_price_at_or_before(
            perp_prices,
            timestamp,
            cursor=perp_cursor,
        )

        for funding_point in funding_by_time.get(timestamp, []):
            payment = (
                quantity
                * perp_point.close
                * funding_point.funding_rate
            )
            # Positive Binance funding means longs pay shorts.
            margin_cash += payment
            funding_income += payment

        futures_unrealized = quantity * (
            short_entry_price - perp_point.close
        )
        margin_equity = margin_cash + futures_unrealized
        minimum_margin_equity = min(
            minimum_margin_equity,
            margin_equity,
        )

        if (
            quantity > 0
            and margin_equity
            < initial_margin * REBALANCE_TRIGGER_FRACTION
        ):
            deficit = initial_margin - margin_equity
            net_cash_per_btc = (
                spot_point.close
                * (Decimal("1") - cost_model.spot_execution_rate)
                - perp_point.close
                * cost_model.futures_execution_rate
            )
            if net_cash_per_btc <= 0:
                raise RuntimeError("Invalid rebalance cash denominator")

            reduce_qty = min(
                quantity,
                deficit / net_cash_per_btc,
            )
            if reduce_qty <= 0 or reduce_qty >= quantity:
                raise RuntimeError(
                    "H5 baseline exhausted the delta-neutral position "
                    f"during rebalance at {timestamp.isoformat()}."
                )

            spot_gross = reduce_qty * spot_point.close
            spot_cost = (
                spot_gross * cost_model.spot_execution_rate
            )
            futures_notional = reduce_qty * perp_point.close
            futures_cost = (
                futures_notional
                * cost_model.futures_execution_rate
            )
            realized_short_pnl = reduce_qty * (
                short_entry_price - perp_point.close
            )

            margin_cash += (
                realized_short_pnl
                + spot_gross
                - spot_cost
                - futures_cost
            )
            quantity -= reduce_qty
            trading_costs += spot_cost + futures_cost
            rebalances += 1
            rebalanced_btc += reduce_qty
            transferred_to_margin += spot_gross - spot_cost

        equity = portfolio_equity(
            spot_point.close,
            perp_point.close,
        )
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            drawdown = (
                (equity_peak - equity)
                / equity_peak
                * Decimal("100")
            )
            max_drawdown = max(max_drawdown, drawdown)

        year = timestamp.year
        yearly_start_equity.setdefault(year, equity)
        yearly_end_equity[year] = equity

    final_spot, spot_cursor = _latest_price_at_or_before(
        spot_prices,
        end_time,
        cursor=spot_cursor,
    )
    final_perp, perp_cursor = _latest_price_at_or_before(
        perp_prices,
        end_time,
        cursor=perp_cursor,
    )

    spot_gross = quantity * final_spot.close
    spot_exit_cost = (
        spot_gross * cost_model.spot_execution_rate
    )
    short_notional = quantity * final_perp.close
    futures_exit_cost = (
        short_notional * cost_model.futures_execution_rate
    )
    realized_short_pnl = quantity * (
        short_entry_price - final_perp.close
    )

    ending_equity = (
        margin_cash
        + realized_short_pnl
        + spot_gross
        - spot_exit_cost
        - futures_exit_cost
    )
    trading_costs += spot_exit_cost + futures_exit_cost

    yearly_returns: dict[int, Decimal] = {}
    previous_end: Decimal | None = None
    for year in sorted(yearly_end_equity):
        start_equity = (
            starting_capital
            if previous_end is None
            else previous_end
        )
        end_equity = yearly_end_equity[year]
        yearly_returns[year] = (
            (end_equity / start_equity - Decimal("1"))
            * Decimal("100")
        )
        previous_end = end_equity

    negative_count = sum(
        1
        for item in funding
        if first_time <= item.timestamp <= end_time
        and item.funding_rate < 0
    )
    funding_count = sum(
        1
        for item in funding
        if first_time <= item.timestamp <= end_time
    )
    negative_fraction = (
        Decimal(negative_count) / Decimal(funding_count)
        if funding_count
        else Decimal("0")
    )

    worst_rise = worst_24h_perp_rise_pct(
        [
            item
            for item in perp_prices
            if first_time <= item.timestamp <= end_time
        ]
    )
    static_stress_survives = (
        worst_rise / Decimal("100") * leverage
        < Decimal("1")
    )

    return SimulationResult(
        leverage=leverage,
        cost_model=cost_model.name,
        starting_capital=starting_capital,
        ending_equity=ending_equity,
        net_return_pct=(
            (ending_equity / starting_capital - Decimal("1"))
            * Decimal("100")
        ),
        annualized_return_pct=_annualized_return(
            starting=starting_capital,
            ending=ending_equity,
            start=first_time,
            end=end_time,
        ),
        funding_income=funding_income,
        trading_costs=trading_costs,
        rebalances=rebalances,
        rebalanced_btc=rebalanced_btc,
        transferred_to_margin=transferred_to_margin,
        max_drawdown_pct=max_drawdown,
        negative_funding_fraction=negative_fraction,
        longest_negative_funding_streak_hours=(
            _longest_negative_streak_hours(
                [
                    item
                    for item in funding
                    if first_time <= item.timestamp <= end_time
                ]
            )
        ),
        worst_24h_perp_rise_pct=worst_rise,
        static_margin_stress_survives=static_stress_survives,
        yearly_returns_pct=yearly_returns,
        minimum_margin_equity=minimum_margin_equity,
    )


def _paths(
    *,
    root: Path,
    symbol: str,
    market: str,
    dataset: str,
) -> list[Path]:
    base = root / symbol.upper() / "funding-basis"
    output: list[Path] = []
    for month in month_range(DEVELOPMENT_START, DEVELOPMENT_END):
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
            raise FileNotFoundError(f"Missing H5 input: {path}")
        output.append(path)
    return output


def print_result(
    result: SimulationResult,
    *,
    benchmark_annualized_pct: Decimal,
    benchmark_yearly_pct: dict[int, Decimal],
) -> None:
    excess = (
        result.annualized_return_pct
        - benchmark_annualized_pct
    )
    positive_excess_years = sum(
        1
        for year, strategy_return in result.yearly_returns_pct.items()
        if strategy_return - benchmark_yearly_pct.get(
            year,
            Decimal("0"),
        )
        > 0
    )

    print(
        "H5_RESULT "
        f"leverage={result.leverage}x "
        f"cost_model={result.cost_model} "
        f"net_return={result.net_return_pct:.4f}% "
        f"annualized={result.annualized_return_pct:.4f}% "
        f"benchmark_annualized={benchmark_annualized_pct:.4f}% "
        f"excess_annualized={excess:.4f}pp "
        f"funding_income={result.funding_income:.2f} "
        f"trading_costs={result.trading_costs:.2f} "
        f"max_drawdown={result.max_drawdown_pct:.4f}% "
        f"rebalances={result.rebalances} "
        f"rebalanced_btc={result.rebalanced_btc:.8f} "
        f"margin_transfers={result.transferred_to_margin:.2f} "
        f"negative_funding_fraction="
        f"{result.negative_funding_fraction:.4f} "
        f"longest_negative_streak_hours="
        f"{result.longest_negative_funding_streak_hours:.2f} "
        f"worst_24h_perp_rise={result.worst_24h_perp_rise_pct:.4f}% "
        f"static_margin_stress_survives="
        f"{str(result.static_margin_stress_survives).lower()} "
        f"minimum_margin_equity={result.minimum_margin_equity:.2f}"
    )

    for year in sorted(result.yearly_returns_pct):
        strategy = result.yearly_returns_pct[year]
        benchmark = benchmark_yearly_pct[year]
        print(
            "H5_YEAR "
            f"leverage={result.leverage}x "
            f"cost_model={result.cost_model} "
            f"year={year} "
            f"strategy={strategy:.4f}% "
            f"benchmark={benchmark:.4f}% "
            f"excess={strategy - benchmark:+.4f}pp"
        )

    if (
        result.leverage == Decimal("1")
        and result.cost_model == "detailed_0.38pct"
    ):
        passes = (
            result.net_return_pct > 0
            and excess >= Decimal("3")
            and positive_excess_years >= 3
            and result.static_margin_stress_survives
        )
        print(
            "H5_DEVELOPMENT_GATE "
            f"status={'PASS' if passes else 'FAIL'} "
            f"excess_annualized={excess:.4f}pp "
            f"positive_excess_years={positive_excess_years}/4 "
            f"stress_survives="
            f"{str(result.static_margin_stress_survives).lower()}"
        )


def run(
    *,
    symbol: str,
    input_root: Path,
) -> None:
    symbol = symbol.upper()
    spot_paths = _paths(
        root=input_root,
        symbol=symbol,
        market="spot",
        dataset="klines",
    )
    perp_paths = _paths(
        root=input_root,
        symbol=symbol,
        market="futures/um",
        dataset="klines",
    )
    funding_paths = _paths(
        root=input_root,
        symbol=symbol,
        market="futures/um",
        dataset="fundingRate",
    )

    spot = load_kline_prices(spot_paths)
    perp = load_kline_prices(perp_paths)
    funding = load_funding(funding_paths)
    dtb3_path = (
        input_root
        / symbol
        / "funding-basis/benchmark/DTB3.csv"
    )
    dtb3 = load_dtb3(dtb3_path)
    benchmark_total, benchmark_annualized, benchmark_yearly = (
        benchmark_returns(dtb3)
    )

    print(
        "H5_BENCHMARK "
        f"series=DTB3 "
        f"conversion=simple_daily_rate_over_360 "
        f"cumulative={benchmark_total:.4f}% "
        f"annualized={benchmark_annualized:.4f}%"
    )
    print(
        "H5_INPUT "
        f"symbol={symbol} "
        f"start={DEVELOPMENT_START.isoformat()} "
        f"end={DEVELOPMENT_END.isoformat()} "
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
            result = simulate(
                leverage=leverage,
                spot_prices=spot,
                perp_prices=perp,
                funding=funding,
                cost_model=model,
            )
            print_result(
                result,
                benchmark_annualized_pct=benchmark_annualized,
                benchmark_yearly_pct=benchmark_yearly,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen H5 development-only always-on baseline."
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
