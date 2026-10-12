"""Walk-forward calibration check for direction-agnostic price ranges."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from crypto_signal_engine.forecast.range import PricePoint, evaluate_walk_forward_range


def load_prices(path: Path) -> list[PricePoint]:
    points: list[PricePoint] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            price = payload.get("price")
            timestamp = payload.get("timestamp")
            quality = payload.get("market_data_quality")
            if price is None or timestamp is None:
                continue
            if quality is not None and float(quality) < 1.0:
                continue
            points.append(
                PricePoint(
                    timestamp=datetime.fromisoformat(str(timestamp)),
                    price=float(price),
                )
            )
    return sorted(points, key=lambda item: item.timestamp)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=(
            "runtime-data/backfill/BTCUSDT/materialized/"
            "features-2026-07-01-2026-08-31.jsonl"
        ),
    )
    parser.add_argument("--coverage", type=float, default=0.70)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-calibration", type=int, default=20)
    parser.add_argument("--horizons", default="60,240,1440")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    points = load_prices(Path(args.input))
    print(
        "RANGE_COVERAGE_INPUT "
        f"path={args.input} points={len(points)} "
        "mode=development_diagnostic causal=true direction=false"
    )
    for raw in args.horizons.split(","):
        horizon = int(raw.strip())
        result = evaluate_walk_forward_range(
            points,
            horizon_minutes=horizon,
            target_coverage=args.coverage,
            lookback_days=args.lookback_days,
            minimum_calibration_count=args.min_calibration,
        )
        print(
            "RANGE_COVERAGE "
            f"horizon_min={result.horizon_minutes} "
            f"target={result.target_coverage:.3f} "
            f"evaluated={result.evaluated} "
            f"covered={result.covered} "
            f"empirical={result.empirical_coverage:.4f} "
            f"avg_full_width_pct={result.average_full_width_pct:.4f} "
            f"avg_radius_pct={result.average_radius_pct:.4f}"
        )


if __name__ == "__main__":
    main()
