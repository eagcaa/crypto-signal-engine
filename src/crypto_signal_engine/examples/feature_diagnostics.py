"""Raw feature -> forward return diagnostics on materialized history.

Exploratory research tool for the research/development window only.
It does not use the prediction engine. For each 60-second feature row it
computes the forward spot price return over several horizons and asks a
simple question per feature: do rows with high feature values move
differently from rows with low feature values?

Never run this on a frozen holdout window.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_FEATURES = (
    "spot_cvd_ratio_1m",
    "spot_cvd_ratio_5m",
    "spot_cvd_ratio_15m",
    "futures_cvd_ratio_1m",
    "futures_cvd_ratio_5m",
    "futures_cvd_ratio_15m",
    "trend_score_5m",
    "trend_score_15m",
    "trend_score_1h",
    "trend_score_4h",
)
DEFAULT_HORIZONS_MINUTES = (15, 60, 240)
HOLDOUT_START = "2026-09-01T00:00:00+00:00"


@dataclass(frozen=True, slots=True)
class FeatureDiagnostic:
    feature: str
    horizon_minutes: int
    samples: int
    spearman: float
    top_decile_mean_pct: float
    bottom_decile_mean_pct: float
    spread_pct: float
    first_half_spread_pct: float
    second_half_spread_pct: float


def load_frame(path: Path, features: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            row = {
                "timestamp": item["timestamp"],
                "price": item.get("price"),
                "market_data_quality": item.get("market_data_quality"),
            }
            for name in features:
                row[name] = item.get(name)
            rows.append(row)

    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    numeric = ["price", "market_data_quality", *features]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("timestamp").set_index("timestamp")


def add_forward_returns(
    frame: pd.DataFrame,
    horizons_minutes: tuple[int, ...],
) -> pd.DataFrame:
    result = frame.copy()
    price = result["price"]
    for minutes in horizons_minutes:
        future = price.reindex(price.index.shift(minutes, freq="min"))
        future.index = price.index
        result[f"fwd_{minutes}m"] = (future / price - 1.0) * 100.0
    return result


def _decile_spread(
    values: pd.Series,
    returns: pd.Series,
) -> tuple[float, float, float]:
    low = values.quantile(0.10)
    high = values.quantile(0.90)
    top = returns[values >= high]
    bottom = returns[values <= low]
    if top.empty or bottom.empty:
        return float("nan"), float("nan"), float("nan")
    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())
    return top_mean, bottom_mean, top_mean - bottom_mean


def diagnose(
    frame: pd.DataFrame,
    *,
    features: tuple[str, ...],
    horizons_minutes: tuple[int, ...],
) -> list[FeatureDiagnostic]:
    usable = frame[frame["market_data_quality"] >= 1.0]
    midpoint = (
        usable.index.min()
        + (usable.index.max() - usable.index.min()) / 2
    )
    output: list[FeatureDiagnostic] = []

    for feature in features:
        for minutes in horizons_minutes:
            column = f"fwd_{minutes}m"
            subset = usable[[feature, column]].dropna()
            if len(subset) < 100 or subset[feature].nunique() < 10:
                continue

            spearman = float(
                subset[feature].rank().corr(subset[column].rank())
            )
            top, bottom, spread = _decile_spread(
                subset[feature],
                subset[column],
            )

            first = subset[subset.index < midpoint]
            second = subset[subset.index >= midpoint]
            _, _, first_spread = _decile_spread(
                first[feature],
                first[column],
            )
            _, _, second_spread = _decile_spread(
                second[feature],
                second[column],
            )

            output.append(
                FeatureDiagnostic(
                    feature=feature,
                    horizon_minutes=minutes,
                    samples=len(subset),
                    spearman=spearman,
                    top_decile_mean_pct=top,
                    bottom_decile_mean_pct=bottom,
                    spread_pct=spread,
                    first_half_spread_pct=first_spread,
                    second_half_spread_pct=second_spread,
                )
            )
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
        "--input",
        required=True,
        help="Materialized JSONL path",
    )
    parser.add_argument(
        "--features",
        default=",".join(DEFAULT_FEATURES),
    )
    parser.add_argument(
        "--horizons",
        default=",".join(
            str(item)
            for item in DEFAULT_HORIZONS_MINUTES
        ),
        help="Forward horizons in minutes, comma separated",
    )
    parser.add_argument(
        "--cost-pct",
        type=float,
        default=0.12,
    )
    args = parser.parse_args()

    features = _parse_csv(args.features, str)
    horizons = _parse_csv(args.horizons, int)

    raw = load_frame(Path(args.input), features)
    holdout_start = pd.Timestamp(HOLDOUT_START)
    if (raw.index >= holdout_start).any():
        raise SystemExit(
            "Refusing to run: input contains rows at or after "
            f"the frozen holdout start {HOLDOUT_START}. "
            "Use research-window data only."
        )

    frame = add_forward_returns(raw, horizons)
    results = diagnose(
        frame,
        features=features,
        horizons_minutes=horizons,
    )

    print(
        "FEATURE_DIAGNOSTICS "
        f"input={args.input} "
        f"rows={len(frame)} "
        f"round_trip_cost={args.cost_pct:.4f}%"
    )
    print(
        "NOTE spread = mean forward return of top decile "
        "minus bottom decile. A tradeable one-sided edge "
        "needs roughly |spread|/2 > cost; half1/half2 "
        "must agree in sign to count as stable."
    )

    for item in results:
        stable = (
            np.sign(item.first_half_spread_pct)
            == np.sign(item.second_half_spread_pct)
        )
        clears_cost = (
            abs(item.spread_pct) / 2
            > args.cost_pct
        )
        print(
            f"{item.feature:<24} "
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
