import random
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import exp

from crypto_signal_engine.examples.flow_test import (
    Bar,
    analyze,
    build_samples,
    candidates,
    render,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(count: int, *, predictive: bool, seed: int = 7) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = 100.0
    shares = [rng.uniform(0.35, 0.65) for _ in range(count)]
    for index in range(count):
        drift = 0.0
        if predictive and index >= 3:
            recent = sum(shares[index - 3 : index]) / 3
            drift = (recent - 0.5) * 0.02
        price *= exp(drift + rng.gauss(0, 0.002))
        bars.append(
            Bar(
                open_time=T0 + timedelta(minutes=5 * index),
                close=price,
                volume=10.0,
                taker_buy=10.0 * shares[index],
            )
        )
    return bars


def test_samples_do_not_overlap_forward_windows() -> None:
    bars = _bars(200, predictive=False)
    samples = build_samples(bars, window=3, horizon=12)
    gaps = {(b.time - a.time) for a, b in pairwise(samples)}
    assert gaps == {timedelta(minutes=60)}
    assert all(0.0 <= s.share <= 1.0 for s in samples)


def test_random_flow_produces_no_candidate() -> None:
    results, correlations = analyze("RNDUSDT", _bars(20_000, predictive=False))
    assert results and correlations
    assert candidates(results) == []
    assert "Aday yok" in render(results, correlations)


def test_predictive_flow_is_detected() -> None:
    results, correlations = analyze("SIGUSDT", _bars(20_000, predictive=True))
    found = candidates(results)
    assert any(r.window == "15dk" and r.horizon == "15dk" for r in found)
    assert "ADAY" in render(results, correlations)


def test_gap_and_duplicate_timestamps_are_rejected() -> None:
    bars = _bars(400, predictive=False)
    for damaged in (
        bars[:100] + bars[101:],
        bars[:100] + [bars[99]] + bars[100:],
    ):
        gaps: list[int] = []
        samples = build_samples(damaged, window=3, horizon=12, gaps=gaps)
        assert gaps
        assert len(samples) < len(build_samples(bars, window=3, horizon=12))
        for sample in samples:
            idx = next(i for i, bar in enumerate(damaged) if bar.open_time == sample.time)
            assert (
                damaged[idx + 12].open_time - damaged[idx].open_time
                == timedelta(minutes=60)
            )


def test_up_down_and_no_call_counts_are_consistent() -> None:
    results, correlations = analyze("SIGUSDT", _bars(20_000, predictive=True))
    for row in results:
        assert row.up_calls + row.down_calls == row.calls
        assert row.up_hits + row.down_hits == row.hits
        assert row.no_calls >= 0
        assert row.total_samples >= row.calls + row.no_calls
        assert 0 <= row.base_up <= 1
    report = render(results, correlations)
    assert "↑ " in report and "↓ " in report
    assert "çağrısız (hareketli)" in report
    assert "Denenen kombinasyon sayısı: 18." in report
