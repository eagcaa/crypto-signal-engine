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
