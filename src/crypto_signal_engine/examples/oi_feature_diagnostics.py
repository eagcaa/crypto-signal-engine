"""OI-only forward-return diagnostics on the July-August research window.

Uses Binance Vision USD-M metrics with an observable-time delay taken from a
metrics alignment artifact. This command accepts PASS_OI_ONLY or PASS artifacts,
but never exposes ratio fields or runs a strategy.
"""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from crypto_signal_engine.examples.feature_diagnostics import (
    HOLDOUT_START,
    add_forward_returns,
    diagnose,
    load_frame,
)
from crypto_signal_engine.features.historical_metrics import (
    HistoricalMetricsTimeline,
    load_metrics_range,
)


OI_FEATURES = (
    "binance_oi_change_5m_pct",
    "binance_oi_change_15m_pct",
)
DEFAULT_HORIZONS_MINUTES = (15, 60, 240)


def load_oi_delay(
    *,
    artifact_path: Path,
    symbol: str,
) -> int:
    payload = json.loads(
        artifact_path.read_text(encoding="utf-8")
    )
    status = payload.get("status")
    if status not in {"PASS", "PASS_OI_ONLY"}:
        raise RuntimeError(
            "OI diagnostics are blocked because metrics alignment "
            f"status is {status!r}."
        )

    artifact_symbol = str(
        payload.get("symbol", "")
    ).upper()
    if artifact_symbol != symbol.upper():
        raise RuntimeError(
            "Metrics alignment artifact symbol mismatch: "
            f"{artifact_symbol or 'missing'} != {symbol.upper()}"
        )

    delay = payload.get("selected_shift_minutes")
    if not isinstance(delay, int) or delay < 0:
        raise RuntimeError(
            "Metrics alignment artifact has no valid OI observable delay."
        )

    return delay


def add_oi_features(
    *,
    frame: pd.DataFrame,
    symbol: str,
    start_day: date,
    end_day: date,
    input_root: Path,
    observable_delay_minutes: int,
) -> pd.DataFrame:
    points = load_metrics_range(
        root=input_root,
        symbol=symbol,
        start_day=start_day - timedelta(days=1),
        end_day=end_day,
    )
    timeline = HistoricalMetricsTimeline(
        points,
        observable_shift_minutes=observable_delay_minutes,
    )

    output = frame.copy()
    oi_5m: list[float | None] = []
    oi_15m: list[float | None] = []

    for timestamp in output.index:
        now = timestamp.to_pydatetime()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        metrics = timeline.at(now.astimezone(UTC))
        oi_5m.append(
            None
            if metrics is None
            or metrics.oi_change_5m_pct is None
            else float(metrics.oi_change_5m_pct)
        )
        oi_15m.append(
            None
            if metrics is None
            or metrics.oi_change_15m_pct is None
            else float(metrics.oi_change_15m_pct)
        )

    output["binance_oi_change_5m_pct"] = oi_5m
    output["binance_oi_change_15m_pct"] = oi_15m
    return output


def _parse_csv(value: str, cast):
    return tuple(
        cast(item.strip())
        for item in value.split(",")
        if item.strip()
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
        "--input",
        required=True,
        help="July-August materialized v1 JSONL path.",
    )
    parser.add_argument(
        "--metrics-alignment-artifact",
        required=True,
    )
    parser.add_argument(
        "--start-date",
        required=True,
    )
    parser.add_argument(
        "--end-date",
        required=True,
    )
    parser.add_argument(
        "--input-root",
        default="runtime-data/backfill",
    )
    parser.add_argument(
        "--horizons",
        default=",".join(
            str(item)
            for item in DEFAULT_HORIZONS_MINUTES
        ),
    )
    parser.add_argument(
        "--cost-pct",
        type=float,
        default=0.12,
    )
    args = parser.parse_args()

    start_day = date.fromisoformat(
        args.start_date
    )
    end_day = date.fromisoformat(
        args.end_date
    )
    holdout_start = pd.Timestamp(
        HOLDOUT_START
    )
    if (
        pd.Timestamp(
            datetime.combine(
                end_day,
                datetime.max.time(),
                tzinfo=UTC,
            )
        )
        >= holdout_start
    ):
        raise SystemExit(
            "Refusing to run: OI diagnostics may inspect only "
            "pre-holdout research data."
        )

    delay = load_oi_delay(
        artifact_path=Path(
            args.metrics_alignment_artifact
        ),
        symbol=args.symbol,
    )

    base = load_frame(
        Path(args.input),
        (),
    )
    if (base.index >= holdout_start).any():
        raise SystemExit(
            "Refusing to run: input contains rows at or after "
            f"the frozen holdout start {HOLDOUT_START}."
        )

    frame = add_oi_features(
        frame=base,
        symbol=args.symbol,
        start_day=start_day,
        end_day=end_day,
        input_root=Path(args.input_root),
        observable_delay_minutes=delay,
    )
    horizons = _parse_csv(
        args.horizons,
        int,
    )
    frame = add_forward_returns(
        frame,
        horizons,
    )
    results = diagnose(
        frame,
        features=OI_FEATURES,
        horizons_minutes=horizons,
    )

    print(
        "OI_FEATURE_DIAGNOSTICS "
        f"symbol={args.symbol.upper()} "
        f"rows={len(frame)} "
        f"observable_delay_minutes={delay} "
        f"round_trip_cost={args.cost_pct:.4f}%"
    )

    for item in results:
        stable = (
            (item.first_half_spread_pct > 0 and item.second_half_spread_pct > 0)
            or (
                item.first_half_spread_pct < 0
                and item.second_half_spread_pct < 0
            )
        )
        clears_cost = (
            abs(item.spread_pct) / 2
            > args.cost_pct
        )
        print(
            f"{item.feature:<28} "
            f"h={item.horizon_minutes:>4}m "
            f"n={item.samples:>6} "
            f"rho={item.spearman:+.4f} "
            f"top={item.top_decile_mean_pct:+.4f}% "
            f"bottom={item.bottom_decile_mean_pct:+.4f}% "
            f"spread={item.spread_pct:+.4f}% "
            f"half1={item.first_half_spread_pct:+.4f}% "
            f"half2={item.second_half_spread_pct:+.4f}% "
            f"stable={'yes' if stable else 'no'} "
            f"clears_cost={'yes' if clears_cost else 'no'}"
        )


if __name__ == "__main__":
    main()
