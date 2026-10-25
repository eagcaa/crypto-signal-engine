from decimal import Decimal

import pytest

from crypto_signal_engine.db.range_forecast_repository import RangeCoverageStat
from crypto_signal_engine.examples.market_watch import (
    _calibration_label,
    _scorecard_score,
    format_range_scorecard,
    format_range_stats,
)


class FakeStatsRepository:
    async def coverage_stats(self, *, symbol=None):
        if symbol == "EMPTYUSDT":
            return []
        return [
            RangeCoverageStat(
                symbol="BTCUSDT",
                horizon_minutes=15,
                evaluated=10,
                covered=7,
                coverage_pct=Decimal("70"),
                pending=2,
                no_data=0,
                independent=10,
                independent_covered=7,
                avg_band_radius_pct=0.55,
                avg_overshoot_pct=0.12,
                max_overshoot_pct=0.31,
                upward_breaches=2,
                downward_breaches=1,
            ),
            RangeCoverageStat(
                symbol="BTCUSDT",
                horizon_minutes=60,
                evaluated=54,
                covered=39,
                coverage_pct=Decimal("72.22"),
                pending=1,
                no_data=1,
                independent=54,
                independent_covered=39,
                avg_band_radius_pct=0.81,
                avg_overshoot_pct=0.18,
                max_overshoot_pct=0.61,
                upward_breaches=8,
                downward_breaches=7,
            ),
        ]


@pytest.mark.asyncio
async def test_format_range_stats_renders_scorecard_metrics() -> None:
    text = await format_range_stats(FakeStatsRepository(), "BTCUSDT")

    assert "BTCUSDT · 15 Dakikalık Tahmin Karnesi" in text
    assert "BTCUSDT · 1 Saatlik Tahmin Karnesi" in text
    assert "54 bağımsız tahmin" in text
    assert "39 tanesi bant içinde kaldı (%72)" in text
    assert "Ortalama bant: ±%0.81" in text
    assert "ortalama taşma: %0.18" in text
    assert "En kötü taşma: %0.61" in text
    assert "Taşma yönü: ↑ 8 · ↓ 7" in text
    assert "Kalibrasyon: Güçlü veri" in text
    assert "hedef %70 kapsama" in text


@pytest.mark.asyncio
async def test_compact_scorecard_prefers_one_hour_horizon() -> None:
    text = await format_range_scorecard(FakeStatsRepository(), "BTCUSDT")

    assert text is not None
    assert "BTCUSDT · 1 Saatlik Tahmin Karnesi" in text
    assert "54 bağımsız tahmin" in text
    assert "15 Dakikalık" not in text
    assert "/stats BTC" in text


@pytest.mark.asyncio
async def test_format_range_stats_handles_empty_result() -> None:
    text = await format_range_stats(FakeStatsRepository(), "EMPTYUSDT")
    assert text == "📊 EMPTYUSDT için forward sonuç yok."


@pytest.mark.asyncio
async def test_compact_scorecard_handles_no_evaluated_result() -> None:
    text = await format_range_scorecard(FakeStatsRepository(), "EMPTYUSDT")
    assert text is None


def test_score_is_sample_size_aware() -> None:
    small = RangeCoverageStat(
        symbol="BTCUSDT",
        horizon_minutes=60,
        evaluated=8,
        covered=6,
        coverage_pct=Decimal("75"),
        pending=0,
        no_data=0,
        independent=8,
        independent_covered=6,
        avg_band_radius_pct=0.8,
        avg_overshoot_pct=0.1,
        max_overshoot_pct=0.2,
    )
    mature = RangeCoverageStat(
        symbol="BTCUSDT",
        horizon_minutes=60,
        evaluated=60,
        covered=45,
        coverage_pct=Decimal("75"),
        pending=0,
        no_data=0,
        independent=60,
        independent_covered=45,
        avg_band_radius_pct=0.8,
        avg_overshoot_pct=0.1,
        max_overshoot_pct=0.2,
    )

    assert _scorecard_score(small) <= 59
    assert _scorecard_score(mature) > _scorecard_score(small)
    assert _calibration_label(8) == "Az veri"
    assert _calibration_label(25) == "Orta veri"
    assert _calibration_label(60) == "Güçlü veri"


def test_count_independent_drops_overlapping_requests() -> None:
    from datetime import UTC, datetime, timedelta

    from crypto_signal_engine.db.range_forecast_repository import count_independent

    t0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    outcomes = [
        (t0, True),
        (t0 + timedelta(minutes=2), True),
        (t0 + timedelta(minutes=10), False),
        (t0 + timedelta(minutes=15), False),
        (t0 + timedelta(minutes=40), True),
    ]
    assert count_independent(outcomes, 15) == (3, 2)
    assert count_independent(outcomes, 60) == (1, 1)
