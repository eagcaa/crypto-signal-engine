import argparse
import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.examples.backfill_history import (
    TECHNICAL_WARMUP_DAYS,
    requested_days,
)
from crypto_signal_engine.examples.materialize_history import (
    INTERVALS,
    PeekableTrades,
    RollingTradeWindow,
    TechnicalTimeline,
    _archive_path,
    _json_safe,
    build_historical_snapshot,
    iter_agg_trades,
    load_klines,
)
from crypto_signal_engine.features.historical_metrics import (
    HistoricalMetricsTimeline,
    load_metrics_range,
)


PROVENANCE_V2 = "binance_vision_historical_compatible_v2"


def load_alignment_shift(
    *,
    artifact_path: Path,
    symbol: str,
) -> int:
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Metrics alignment artifact not found: {artifact_path}"
        )

    payload = json.loads(
        artifact_path.read_text(encoding="utf-8")
    )
    if payload.get("status") != "PASS":
        raise RuntimeError(
            "Historical v2 is blocked because metrics alignment "
            f"status is {payload.get('status')!r}, not 'PASS'."
        )

    artifact_symbol = str(payload.get("symbol", "")).upper()
    if artifact_symbol != symbol.upper():
        raise RuntimeError(
            "Metrics alignment artifact symbol mismatch: "
            f"{artifact_symbol or 'missing'} != {symbol.upper()}"
        )

    shift = payload.get("selected_shift_minutes")
    if not isinstance(shift, int):
        raise RuntimeError(
            "Metrics alignment artifact has no selected integer shift."
        )

    if shift not in {-5, 0, 5}:
        raise RuntimeError(
            f"Unsupported metrics alignment shift: {shift}"
        )

    return shift


def materialize_v2(
    *,
    symbol: str,
    days: int,
    end_day: date,
    input_root: Path,
    alignment_artifact: Path,
    output_path: Path | None = None,
) -> Path:
    days_to_process = requested_days(
        days=days,
        end_day=end_day,
    )
    symbol = symbol.upper()
    shift_minutes = load_alignment_shift(
        artifact_path=alignment_artifact,
        symbol=symbol,
    )

    spot_paths = [
        _archive_path(
            input_root,
            symbol=symbol,
            day=day,
            market="spot",
            dataset="aggTrades",
        )
        for day in days_to_process
    ]
    futures_paths = [
        _archive_path(
            input_root,
            symbol=symbol,
            day=day,
            market="futures",
            dataset="aggTrades",
        )
        for day in days_to_process
    ]

    warmup_start = (
        days_to_process[0]
        - timedelta(days=TECHNICAL_WARMUP_DAYS)
    )
    kline_days = requested_days(
        days=days + TECHNICAL_WARMUP_DAYS,
        end_day=end_day,
    )
    assert kline_days[0] == warmup_start

    timelines: dict[str, TechnicalTimeline] = {}
    for interval in INTERVALS:
        kline_paths = [
            _archive_path(
                input_root,
                symbol=symbol,
                day=day,
                market="spot",
                dataset="klines",
                interval=interval,
            )
            for day in kline_days
        ]
        timelines[interval] = TechnicalTimeline(
            load_klines(
                kline_paths,
                symbol=symbol,
                interval=interval,
            )
        )

    metrics_points = load_metrics_range(
        root=input_root,
        symbol=symbol,
        start_day=days_to_process[0] - timedelta(days=1),
        end_day=days_to_process[-1],
    )
    metrics_timeline = HistoricalMetricsTimeline(
        metrics_points,
        observable_shift_minutes=shift_minutes,
    )

    spot_stream = PeekableTrades(
        iter_agg_trades(spot_paths)
    )
    futures_stream = PeekableTrades(
        iter_agg_trades(futures_paths)
    )
    spot_window = RollingTradeWindow()
    futures_window = RollingTradeWindow()

    start_timestamp = datetime.combine(
        days_to_process[0],
        datetime.min.time(),
        tzinfo=UTC,
    )
    end_timestamp = (
        datetime.combine(
            days_to_process[-1],
            datetime.min.time(),
            tzinfo=UTC,
        )
        + timedelta(days=1)
        - timedelta(minutes=1)
    )

    if output_path is None:
        output_dir = input_root / symbol / "materialized"
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path = output_dir / (
            f"features-v2-{days_to_process[0].isoformat()}-"
            f"{days_to_process[-1].isoformat()}.jsonl"
        )
    else:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    row_count = 0
    usable_count = 0
    full_quality_count = 0
    metrics_ready_count = 0
    current = start_timestamp

    with output_path.open("w", encoding="utf-8") as handle:
        while current <= end_timestamp:
            spot_window.add_many(
                spot_stream.consume_through(current)
            )
            futures_window.add_many(
                futures_stream.consume_through(current)
            )

            technicals = {
                interval: timeline.at(current)
                for interval, timeline in timelines.items()
            }
            metrics = metrics_timeline.at(current)

            snapshot = build_historical_snapshot(
                symbol=symbol,
                timestamp=current,
                start_timestamp=start_timestamp,
                spot_window=spot_window,
                futures_window=futures_window,
                technicals=technicals,
            )

            if metrics is not None:
                snapshot = replace(
                    snapshot,
                    binance_oi_change_5m_pct=(
                        metrics.oi_change_5m_pct
                    ),
                    binance_oi_change_15m_pct=(
                        metrics.oi_change_15m_pct
                    ),
                    binance_long_short_ratio=(
                        metrics.long_short_ratio
                    ),
                    binance_top_trader_long_short_ratio=(
                        metrics.top_trader_long_short_ratio
                    ),
                    binance_taker_buy_sell_ratio=(
                        metrics.taker_buy_sell_ratio
                    ),
                    dataset_provenance=PROVENANCE_V2,
                )
            else:
                snapshot = replace(
                    snapshot,
                    dataset_provenance=PROVENANCE_V2,
                )

            handle.write(
                json.dumps(
                    _json_safe(asdict(snapshot)),
                    sort_keys=True,
                )
                + "\n"
            )

            row_count += 1
            if snapshot.market_data_quality == Decimal("1"):
                full_quality_count += 1

            metrics_ready = (
                snapshot.binance_oi_change_15m_pct
                is not None
                and snapshot.binance_long_short_ratio
                is not None
                and snapshot.binance_top_trader_long_short_ratio
                is not None
                and snapshot.binance_taker_buy_sell_ratio
                is not None
            )
            if metrics_ready:
                metrics_ready_count += 1

            if (
                snapshot.price is not None
                and snapshot.market_data_quality
                == Decimal("1")
                and snapshot.history_seconds >= 900
                and snapshot.trend_regime_15m
                is not None
                and snapshot.volatility_regime_15m
                is not None
                and metrics_ready
            ):
                usable_count += 1

            current += timedelta(minutes=1)

    print(
        "BACKFILL_MATERIALIZED_V2 "
        f"symbol={symbol} "
        f"rows={row_count} "
        f"full_quality={full_quality_count} "
        f"metrics_ready={metrics_ready_count} "
        f"usable={usable_count} "
        f"technical_warmup_days={TECHNICAL_WARMUP_DAYS} "
        f"metrics_enabled=true "
        f"observable_shift_minutes={shift_minutes:+d} "
        f"provenance={PROVENANCE_V2} "
        f"output={output_path}"
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize historical-compatible v2 features. "
            "Requires a PASS metrics timestamp-alignment artifact."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=62)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--input-root",
        default="runtime-data/backfill",
    )
    parser.add_argument(
        "--metrics-alignment-artifact",
        required=True,
    )
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    materialize_v2(
        symbol=args.symbol,
        days=args.days,
        end_day=date.fromisoformat(args.end_date),
        input_root=Path(args.input_root),
        alignment_artifact=Path(
            args.metrics_alignment_artifact
        ),
        output_path=(
            Path(args.output)
            if args.output
            else None
        ),
    )


if __name__ == "__main__":
    main()
