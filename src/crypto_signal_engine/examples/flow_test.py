"""Offline test: does Binance spot taker flow predict the next price move?

For each coin, past 5m klines are downloaded (they carry taker-buy volume).
For every combination of a flow window (15m / 1h / 4h) and a forward horizon
(15m / 1h / 4h) the test asks: when aggressive buying dominated, did price
rise over the *next* horizon more often than chance?

Honesty rules built in:

- Samples are spaced by the horizon, so forward windows never overlap.
- The history is split in half by time. Quantile thresholds are learned on the
  first half only and judged on the second half (no look-ahead).
- "Chance" is not 50%: it is the hit rate a coin-flip with the same up/down
  base rate would get on exactly the same calls.
- Many combinations are tried, so a row is only a candidate when z >= 3 on the
  test half AND the edge has the same sign on the training half.
- The contemporaneous correlation (flow vs. the move that already happened)
  is printed next to the predictive one, to show how much of the flow simply
  coincides with the move instead of leading it.

Usage:
    python -m crypto_signal_engine.examples.flow_test --symbols BTCUSDT,ETHUSDT --days 180
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from math import log, sqrt

BAR_MINUTES = 5
WINDOWS = {"15dk": 3, "1s": 12, "4s": 48}
HORIZONS = {"15dk": 3, "1s": 12, "4s": 48}
Z_CANDIDATE = 3.0


@dataclass(frozen=True, slots=True)
class Bar:
    open_time: datetime
    close: float
    volume: float
    taker_buy: float


@dataclass(frozen=True, slots=True)
class FlowResult:
    symbol: str
    window: str
    horizon: str
    rule: str
    period: str
    calls: int
    hits: int
    hit_rate: float
    expected: float
    edge: float
    z: float
    ci_low: float
    ci_high: float
    up_calls: int = 0
    up_hits: int = 0
    down_calls: int = 0
    down_hits: int = 0
    no_calls: int = 0
    base_up: float = 0.5
    total_samples: int = 0


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    symbol: str
    window: str
    horizon: str
    samples: int
    predictive: float
    contemporaneous: float
    skipped_gaps: int = 0


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / sqrt(sxx * syy)


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class _Sample:
    time: datetime
    share: float
    past: float
    forward: float


def _contiguous(bars: Sequence[Bar], start: int, end: int) -> bool:
    """Check every adjacent timestamp, including duplicates and out-of-order bars."""
    step = timedelta(minutes=BAR_MINUTES)
    return all(
        bars[i + 1].open_time - bars[i].open_time == step
        for i in range(start, end)
    )


def build_samples(
    bars: Sequence[Bar],
    window: int,
    horizon: int,
    gaps: list[int] | None = None,
) -> list[_Sample]:
    samples: list[_Sample] = []
    index = window
    while index + horizon < len(bars):
        if not _contiguous(bars, index - window, index + horizon):
            if gaps is not None:
                gaps.append(index)
            index += horizon
            continue
        chunk = bars[index - window + 1 : index + 1]
        volume = sum(bar.volume for bar in chunk)
        anchor = bars[index].close
        before = bars[index - window].close
        after = bars[index + horizon].close
        if volume > 0 and anchor > 0 and before > 0 and after > 0:
            samples.append(
                _Sample(
                    time=bars[index].open_time,
                    share=sum(bar.taker_buy for bar in chunk) / volume,
                    past=log(anchor / before),
                    forward=log(after / anchor),
                )
            )
        index += horizon
    return samples


@dataclass(frozen=True, slots=True)
class _Score:
    calls: int
    hits: int
    expected: float
    up_calls: int
    up_hits: int
    down_calls: int
    down_hits: int
    no_calls: int
    base_up: float
    total_samples: int


def _score(samples: Sequence[_Sample], high: float, low: float) -> _Score:
    """Calls determine hit rate; all moving samples determine the base rate."""
    moving = [item for item in samples if item.forward != 0]
    if not moving:
        return _Score(0, 0, 0.5, 0, 0, 0, 0, 0, 0.5, len(samples))
    up_rate = sum(item.forward > 0 for item in moving) / len(moving)
    up_calls = up_hits = down_calls = down_hits = 0
    for sample in moving:
        if sample.share >= high:
            up_calls += 1
            up_hits += int(sample.forward > 0)
        elif sample.share <= low:
            down_calls += 1
            down_hits += int(sample.forward < 0)
    calls = up_calls + down_calls
    expected = (
        (up_calls * up_rate + down_calls * (1 - up_rate)) / calls
        if calls else 0.5
    )
    return _Score(
        calls, up_hits + down_hits, expected,
        up_calls, up_hits, down_calls, down_hits,
        len(moving) - calls, up_rate, len(samples),
    )


def _result(
    symbol: str,
    window: str,
    horizon: str,
    rule: str,
    period: str,
    samples: Sequence[_Sample],
    high: float,
    low: float,
) -> FlowResult:
    score = _score(samples, high, low)
    calls, hits, expected = score.calls, score.hits, score.expected
    rate = hits / calls if calls else 0.0
    spread = sqrt(calls * expected * (1 - expected)) if calls else 0.0
    z = (hits - calls * expected) / spread if spread > 0 else 0.0
    ci_low, ci_high = _wilson(hits, calls)
    return FlowResult(
        symbol=symbol,
        window=window,
        horizon=horizon,
        rule=rule,
        period=period,
        calls=calls,
        hits=hits,
        hit_rate=rate,
        expected=expected,
        edge=rate - expected,
        z=z,
        ci_low=ci_low,
        ci_high=ci_high,
        up_calls=score.up_calls,
        up_hits=score.up_hits,
        down_calls=score.down_calls,
        down_hits=score.down_hits,
        no_calls=score.no_calls,
        base_up=score.base_up,
        total_samples=score.total_samples,
    )


def analyze(
    symbol: str,
    bars: Sequence[Bar],
) -> tuple[list[FlowResult], list[CorrelationResult]]:
    results: list[FlowResult] = []
    correlations: list[CorrelationResult] = []
    for window_label, window in WINDOWS.items():
        for horizon_label, horizon in HORIZONS.items():
            gaps: list[int] = []
            samples = build_samples(bars, window, horizon, gaps)
            if len(samples) < 40:
                continue
            middle = len(samples) // 2
            train, test = samples[:middle], samples[middle:]

            correlations.append(
                CorrelationResult(
                    symbol=symbol,
                    window=window_label,
                    horizon=horizon_label,
                    samples=len(samples),
                    predictive=_corr(
                        [s.share for s in samples], [s.forward for s in samples]
                    ),
                    contemporaneous=_corr(
                        [s.share for s in samples], [s.past for s in samples]
                    ),
                    skipped_gaps=len(gaps),
                )
            )

            shares = [s.share for s in train]
            rules = {
                "sabit %55/%45": (0.55, 0.45),
                "en uç %20": (_quantile(shares, 0.80), _quantile(shares, 0.20)),
            }
            for rule, (high, low) in rules.items():
                for period, chunk in (("eğitim", train), ("test", test)):
                    results.append(
                        _result(
                            symbol,
                            window_label,
                            horizon_label,
                            rule,
                            period,
                            chunk,
                            high,
                            low,
                        )
                    )
    return results, correlations


def candidates(results: Sequence[FlowResult]) -> list[FlowResult]:
    by_key = {
        (r.symbol, r.window, r.horizon, r.rule, r.period): r for r in results
    }
    found = []
    for r in results:
        if r.period != "test" or r.calls < 30 or r.z < Z_CANDIDATE:
            continue
        train = by_key.get((r.symbol, r.window, r.horizon, r.rule, "eğitim"))
        if train is not None and train.edge > 0:
            found.append(r)
    return found


def _direction_part(arrow: str, hits: int, calls: int, chance: float) -> str:
    if not calls:
        return f"{arrow} 0 çağrı"
    return (
        f"{arrow} {calls} çağrı, isabet %{hits / calls * 100:.1f} "
        f"(şans %{chance * 100:.1f})"
    )


def render(
    results: Sequence[FlowResult],
    correlations: Sequence[CorrelationResult],
) -> str:
    found = candidates(results)
    lines = ["AKIŞ → YÖN TESTİ (Binance Spot taker akışı)", ""]
    lines.append(
        "Sütunlar: isabet = akış yönünde tahmin tutma oranı; şans = aynı "
        "çağrılarda yazı-tura beklentisi; z>=3 ve eğitimde de pozitifse aday."
    )
    for symbol in sorted({r.symbol for r in results}):
        lines.extend(["", f"=== {symbol} ==="])
        lines.append("Korelasyon (akış ↔ sonraki hareket | akış ↔ zaten olan hareket)")
        for c in correlations:
            if c.symbol == symbol:
                gap_note = (
                    f", boşluk nedeniyle atlanan {c.skipped_gaps}"
                    if c.skipped_gaps else ""
                )
                lines.append(
                    f"  akış {c.window:>4} → sonraki {c.horizon:>4}: "
                    f"{c.predictive:+.3f} | {c.contemporaneous:+.3f}  "
                    f"(n={c.samples}{gap_note})"
                )
        lines.append("Test yarısı (eşikler eğitim yarısından):")
        for r in results:
            if r.symbol != symbol or r.period != "test":
                continue
            flag = "  ← ADAY" if r in found else ""
            lines.append(
                f"  akış {r.window:>4} → {r.horizon:>4} · {r.rule:<13} · "
                f"çağrı {r.calls:>4} · isabet %{r.hit_rate * 100:4.1f} "
                f"(%{r.ci_low * 100:.0f}–{r.ci_high * 100:.0f}) · "
                f"şans %{r.expected * 100:4.1f} · z {r.z:+.1f}{flag}"
            )
            lines.append(
                "      "
                + _direction_part("↑", r.up_hits, r.up_calls, r.base_up)
                + " · "
                + _direction_part("↓", r.down_hits, r.down_calls, 1 - r.base_up)
                + f" · çağrısız (hareketli) {r.no_calls}"
                + f" · toplam örnek {r.total_samples}"
            )

    tested = sum(1 for r in results if r.period == "test")
    lines.extend(["", "SONUÇ", f"Denenen kombinasyon sayısı: {tested}."])
    if not found:
        lines.append(
            "Aday yok: akış, sonraki fiyat yönünü şanstan anlamlı biçimde "
            "daha iyi tahmin etmiyor. Bot'ta yön tahmini olarak sunulmamalı."
        )
    else:
        lines.append(
            f"{len(found)} aday kombinasyon var. Bunlar kesin sonuç değil; "
            "canlı ölçümle doğrulanmadan yön sinyali olarak sunulmamalı:"
        )
        for r in found:
            lines.append(
                f"  {r.symbol} akış {r.window} → {r.horizon} ({r.rule}): "
                f"isabet %{r.hit_rate * 100:.1f} vs şans %{r.expected * 100:.1f}, "
                f"z {r.z:+.1f}, {r.calls} çağrı"
            )
    return "\n".join(lines)


async def fetch_bars(symbol: str, days: int) -> list[Bar]:
    from crypto_signal_engine.collectors.binance import BinanceSpotCandleClient

    client = BinanceSpotCandleClient(timeout_seconds=20.0)
    now = datetime.now(UTC)
    cursor = now - timedelta(days=days)
    bars: list[Bar] = []
    while cursor < now - timedelta(minutes=BAR_MINUTES):
        candles = await client.fetch_closed(
            symbol, interval=f"{BAR_MINUTES}m", limit=999, start_time=cursor
        )
        fresh = [c for c in candles if not bars or c.open_time > bars[-1].open_time]
        if not fresh:
            break
        bars.extend(
            Bar(
                open_time=c.open_time,
                close=float(c.close),
                volume=float(c.volume),
                taker_buy=float(c.taker_buy_base_volume),
            )
            for c in fresh
        )
        cursor = fresh[-1].open_time + timedelta(minutes=BAR_MINUTES)
        await asyncio.sleep(0.2)
    return bars


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT",
    )
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--csv", default="", help="optional CSV output path")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    all_results: list[FlowResult] = []
    all_corr: list[CorrelationResult] = []
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        print(f"{symbol}: {args.days} günlük 5dk mum indiriliyor...", flush=True)
        bars = await fetch_bars(symbol, args.days)
        print(f"{symbol}: {len(bars)} mum", flush=True)
        results, correlations = analyze(symbol, bars)
        all_results.extend(results)
        all_corr.extend(correlations)
    print()
    print(render(all_results, all_corr))
    if args.csv:
        write_csv(args.csv, all_results)
        print(f"\nCSV yazıldı: {args.csv}")


def write_csv(path: str, results: Sequence[FlowResult]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FlowResult.__annotations__))
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))


def main() -> None:
    asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    main()
