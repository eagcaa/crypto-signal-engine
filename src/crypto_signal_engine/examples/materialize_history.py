import argparse
import csv
import json
import zipfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path
from typing import Iterator

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.examples.backfill_history import (
    TECHNICAL_WARMUP_DAYS,
    archive_timestamp_to_datetime,
    requested_days,
)
from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.features.technical import (
    TechnicalFeatureSnapshot,
    build_technical_features,
)


PROVENANCE = "binance_vision_historical_compatible_v1"
INTERVALS = ("5m", "15m", "1h", "4h")
TECHNICAL_CANDLE_COUNT = 100
SOURCE_FRESHNESS_SECONDS = 60


class HistoricalTrade:
    __slots__ = ("timestamp", "price", "signed_quantity", "absolute_quantity")

    def __init__(
        self,
        timestamp: datetime,
        price: Decimal,
        signed_quantity: Decimal,
    ) -> None:
        self.timestamp = timestamp
        self.price = price
        self.signed_quantity = signed_quantity
        self.absolute_quantity = abs(signed_quantity)


@dataclass(slots=True)
class MinuteTradeBucket:
    minute: datetime
    signed_quantity: Decimal = Decimal("0")
    absolute_quantity: Decimal = Decimal("0")
    last_price: Decimal | None = None
    last_trade_at: datetime | None = None

    def add(self, trade: HistoricalTrade) -> None:
        self.signed_quantity += trade.signed_quantity
        self.absolute_quantity += trade.absolute_quantity
        self.last_price = trade.price
        self.last_trade_at = trade.timestamp


class PeekableTrades:
    def __init__(self, iterator: Iterator[HistoricalTrade]) -> None:
        self._iterator = iterator
        self._next = next(iterator, None)

    def consume_through(self, timestamp: datetime) -> list[HistoricalTrade]:
        items: list[HistoricalTrade] = []
        while self._next is not None and self._next.timestamp <= timestamp:
            items.append(self._next)
            self._next = next(self._iterator, None)
        return items


class RollingTradeWindow:
    """Minute-bucketed rolling flow for 60-second historical snapshots."""

    def __init__(self) -> None:
        self._buckets: deque[MinuteTradeBucket] = deque()
        self.latest_price: Decimal | None = None
        self.last_trade_at: datetime | None = None

    @staticmethod
    def _minute_floor(timestamp: datetime) -> datetime:
        return timestamp.replace(second=0, microsecond=0)

    def add_many(self, trades: list[HistoricalTrade]) -> None:
        for trade in trades:
            minute = self._minute_floor(trade.timestamp)
            if not self._buckets or self._buckets[-1].minute != minute:
                self._buckets.append(MinuteTradeBucket(minute=minute))
            self._buckets[-1].add(trade)
            self.latest_price = trade.price
            self.last_trade_at = trade.timestamp

    def trim(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=15)
        cutoff_minute = self._minute_floor(cutoff)
        while self._buckets and self._buckets[0].minute < cutoff_minute:
            self._buckets.popleft()

    def is_fresh(
        self,
        now: datetime,
        *,
        freshness_seconds: int = SOURCE_FRESHNESS_SECONDS,
    ) -> bool:
        if self.last_trade_at is None:
            return False
        age = (now - self.last_trade_at).total_seconds()
        return 0 <= age <= freshness_seconds

    def _window_buckets(
        self,
        now: datetime,
        minutes: int,
    ) -> Iterator[MinuteTradeBucket]:
        cutoff = now - timedelta(minutes=minutes)
        cutoff_minute = self._minute_floor(cutoff)
        for bucket in self._buckets:
            if cutoff_minute <= bucket.minute <= now:
                yield bucket

    def cvd(self, now: datetime, minutes: int) -> Decimal:
        return sum(
            (bucket.signed_quantity for bucket in self._window_buckets(now, minutes)),
            Decimal("0"),
        )

    def ratio(self, now: datetime, minutes: int) -> Decimal:
        signed = Decimal("0")
        absolute = Decimal("0")
        for bucket in self._window_buckets(now, minutes):
            signed += bucket.signed_quantity
            absolute += bucket.absolute_quantity
        if absolute == 0:
            return Decimal("0")
        return signed / absolute


class TechnicalTimeline:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = sorted(candles, key=lambda item: item.close_time)
        self._cursor = 0
        self._history: deque[Candle] = deque(maxlen=TECHNICAL_CANDLE_COUNT)

    def at(self, timestamp: datetime) -> TechnicalFeatureSnapshot | None:
        while (
            self._cursor < len(self._candles)
            and self._candles[self._cursor].close_time <= timestamp
        ):
            self._history.append(self._candles[self._cursor])
            self._cursor += 1

        # Live technical polling supplies the most recent 100 closed candles.
        # Do not calculate a historical feature from a shorter warmup.
        if len(self._history) < TECHNICAL_CANDLE_COUNT:
            return None

        return build_technical_features(list(self._history))


def _open_csv_rows(path: Path) -> Iterator[list[str]]:
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
                yield from csv.reader(text)


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return True
    try:
        int(row[0])
        return False
    except ValueError:
        return True


def iter_agg_trades(paths: list[Path]) -> Iterator[HistoricalTrade]:
    previous_timestamp: datetime | None = None

    for path in paths:
        if not path.exists():
            continue

        for row in _open_csv_rows(path):
            if not row or _looks_like_header(row):
                continue
            if len(row) < 7:
                raise ValueError(f"Unexpected aggTrades row in {path}: {row}")

            timestamp = archive_timestamp_to_datetime(row[5])
            price = Decimal(row[1])
            quantity = Decimal(row[2])
            buyer_is_maker = row[6].strip().lower() == "true"
            signed_quantity = -quantity if buyer_is_maker else quantity

            if previous_timestamp is not None and timestamp < previous_timestamp:
                raise ValueError(
                    f"aggTrades not ordered in {path}: "
                    f"{timestamp.isoformat()} < {previous_timestamp.isoformat()}"
                )
            previous_timestamp = timestamp
            yield HistoricalTrade(timestamp, price, signed_quantity)


def load_klines(paths: list[Path], *, symbol: str, interval: str) -> list[Candle]:
    candles: list[Candle] = []

    for path in paths:
        if not path.exists():
            continue
        for row in _open_csv_rows(path):
            if not row or _looks_like_header(row):
                continue
            if len(row) < 7:
                raise ValueError(f"Unexpected kline row in {path}: {row}")
            candles.append(
                Candle(
                    symbol=symbol.upper(),
                    interval=interval,
                    open_time=archive_timestamp_to_datetime(row[0]),
                    close_time=archive_timestamp_to_datetime(row[6]),
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    closed=True,
                )
            )

    candles.sort(key=lambda item: item.close_time)
    return candles


def _archive_path(
    root: Path,
    *,
    symbol: str,
    day: date,
    market: str,
    dataset: str,
    interval: str | None = None,
) -> Path:
    stamp = day.isoformat()
    symbol = symbol.upper()
    if market == "spot" and dataset == "aggTrades":
        return root / symbol / "spot" / "aggTrades" / f"{symbol}-aggTrades-{stamp}.zip"
    if market == "futures" and dataset == "aggTrades":
        return (
            root
            / symbol
            / "futures"
            / "um"
            / "aggTrades"
            / f"{symbol}-aggTrades-{stamp}.zip"
        )
    if market == "spot" and dataset == "klines" and interval is not None:
        return (
            root
            / symbol
            / "spot"
            / "klines"
            / interval
            / f"{symbol}-{interval}-{stamp}.zip"
        )
    raise ValueError("Unsupported archive path request")


def _technical_fields(
    snapshot: TechnicalFeatureSnapshot | None,
) -> tuple[
    Decimal | None,
    Decimal | None,
    str | None,
    str | None,
]:
    if snapshot is None:
        return None, None, None, None
    return (
        snapshot.trend_score,
        snapshot.atr_pct,
        snapshot.trend_regime,
        snapshot.volatility_regime,
    )


def build_historical_snapshot(
    *,
    symbol: str,
    timestamp: datetime,
    start_timestamp: datetime,
    spot_window: RollingTradeWindow,
    futures_window: RollingTradeWindow,
    technicals: dict[str, TechnicalFeatureSnapshot | None],
) -> ResearchFeatureSnapshot:
    spot_window.trim(timestamp)
    futures_window.trim(timestamp)

    spot_fresh = spot_window.is_fresh(timestamp)
    futures_fresh = futures_window.is_fresh(timestamp)
    available_sources = int(spot_fresh) + int(futures_fresh)
    quality = Decimal(available_sources) / Decimal("2")

    trend_5m, atr_5m, regime_5m, vol_5m = _technical_fields(technicals.get("5m"))
    trend_15m, atr_15m, regime_15m, vol_15m = _technical_fields(
        technicals.get("15m")
    )
    trend_1h, atr_1h, regime_1h, vol_1h = _technical_fields(technicals.get("1h"))
    trend_4h, atr_4h, regime_4h, vol_4h = _technical_fields(technicals.get("4h"))

    return ResearchFeatureSnapshot(
        symbol=symbol.upper(),
        timestamp=timestamp,
        price=spot_window.latest_price,
        spot_cvd_1m=spot_window.cvd(timestamp, 1),
        spot_cvd_5m=spot_window.cvd(timestamp, 5),
        spot_cvd_15m=spot_window.cvd(timestamp, 15),
        futures_cvd_1m=futures_window.cvd(timestamp, 1),
        futures_cvd_5m=futures_window.cvd(timestamp, 5),
        futures_cvd_15m=futures_window.cvd(timestamp, 15),
        spot_cvd_ratio_1m=spot_window.ratio(timestamp, 1),
        spot_cvd_ratio_5m=spot_window.ratio(timestamp, 5),
        spot_cvd_ratio_15m=spot_window.ratio(timestamp, 15),
        futures_cvd_ratio_1m=futures_window.ratio(timestamp, 1),
        futures_cvd_ratio_5m=futures_window.ratio(timestamp, 5),
        futures_cvd_ratio_15m=futures_window.ratio(timestamp, 15),
        spot_trade_sources=1 if spot_fresh else 0,
        futures_trade_sources=1 if futures_fresh else 0,
        history_seconds=max(
            0,
            int((timestamp - start_timestamp).total_seconds()),
        ),
        trend_score_5m=trend_5m,
        trend_score_15m=trend_15m,
        trend_score_1h=trend_1h,
        trend_score_4h=trend_4h,
        atr_pct_5m=atr_5m,
        atr_pct_15m=atr_15m,
        atr_pct_1h=atr_1h,
        atr_pct_4h=atr_4h,
        trend_regime_5m=regime_5m,
        trend_regime_15m=regime_15m,
        trend_regime_1h=regime_1h,
        trend_regime_4h=regime_4h,
        volatility_regime_5m=vol_5m,
        volatility_regime_15m=vol_15m,
        volatility_regime_1h=vol_1h,
        volatility_regime_4h=vol_4h,
        binance_oi_change_5m_pct=None,
        binance_oi_change_15m_pct=None,
        bybit_oi_change_5m_pct=None,
        bybit_oi_change_15m_pct=None,
        binance_funding_rate=None,
        bybit_funding_rate=None,
        binance_long_short_ratio=None,
        bybit_long_short_ratio=None,
        binance_top_trader_long_short_ratio=None,
        binance_taker_buy_sell_ratio=None,
        long_liquidations_5m_usd=Decimal("0"),
        short_liquidations_5m_usd=Decimal("0"),
        liquidation_imbalance_5m=Decimal("0"),
        long_liquidations_15m_usd=Decimal("0"),
        short_liquidations_15m_usd=Decimal("0"),
        liquidation_imbalance_15m=Decimal("0"),
        binance_book_imbalance=None,
        bybit_book_imbalance=None,
        market_data_quality=quality,
        liquidation_data_available=False,
        dataset_provenance=PROVENANCE,
    )


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def materialize(
    *,
    symbol: str,
    days: int,
    end_day: date,
    input_root: Path,
    output_path: Path | None = None,
) -> Path:
    days_to_process = requested_days(days=days, end_day=end_day)
    symbol = symbol.upper()

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

    warmup_start = days_to_process[0] - timedelta(days=TECHNICAL_WARMUP_DAYS)
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
            load_klines(kline_paths, symbol=symbol, interval=interval)
        )

    spot_stream = PeekableTrades(iter_agg_trades(spot_paths))
    futures_stream = PeekableTrades(iter_agg_trades(futures_paths))
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
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / (
            f"features-{days_to_process[0].isoformat()}-"
            f"{days_to_process[-1].isoformat()}.jsonl"
        )
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    row_count = 0
    usable_count = 0
    full_quality_count = 0
    current = start_timestamp

    with output_path.open("w", encoding="utf-8") as handle:
        while current <= end_timestamp:
            spot_window.add_many(spot_stream.consume_through(current))
            futures_window.add_many(futures_stream.consume_through(current))

            technicals = {
                interval: timeline.at(current)
                for interval, timeline in timelines.items()
            }

            snapshot = build_historical_snapshot(
                symbol=symbol,
                timestamp=current,
                start_timestamp=start_timestamp,
                spot_window=spot_window,
                futures_window=futures_window,
                technicals=technicals,
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
            if (
                snapshot.price is not None
                and snapshot.market_data_quality == Decimal("1")
                and snapshot.history_seconds >= 900
                and snapshot.trend_regime_15m is not None
                and snapshot.volatility_regime_15m is not None
            ):
                usable_count += 1

            current += timedelta(minutes=1)

    print(
        "BACKFILL_MATERIALIZED "
        f"symbol={symbol} "
        f"rows={row_count} "
        f"full_quality={full_quality_count} "
        f"usable={usable_count} "
        f"technical_warmup_days={TECHNICAL_WARMUP_DAYS} "
        f"metrics_enabled=false "
        f"output={output_path}"
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert downloaded Binance Vision archives into 60-second "
            "historical-compatible research feature snapshots."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--input-root", default="runtime-data/backfill")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    materialize(
        symbol=args.symbol,
        days=args.days,
        end_day=date.fromisoformat(args.end_date),
        input_root=Path(args.input_root),
        output_path=Path(args.output) if args.output else None,
    )


if __name__ == "__main__":
    main()
