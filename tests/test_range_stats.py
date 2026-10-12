from decimal import Decimal

import pytest

from crypto_signal_engine.db.range_forecast_repository import RangeCoverageStat
from crypto_signal_engine.examples.market_watch import format_range_stats


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
            ),
            RangeCoverageStat(
                symbol="BTCUSDT",
                horizon_minutes=60,
                evaluated=4,
                covered=3,
                coverage_pct=Decimal("75"),
                pending=1,
                no_data=1,
                independent=3,
                independent_covered=2,
            ),
        ]


@pytest.mark.asyncio
async def test_format_range_stats_renders_coverage_and_statuses() -> None:
    text = await format_range_stats(FakeStatsRepository(), "BTCUSDT")

    assert "BTCUSDT" in text
    assert "15 dk: %70 tuttu (7/10, ±28 puan)" in text
    assert "1 saat: %67 tuttu (2/3, ±53 puan) [toplam 4 kayıt]" in text
    assert "2 bekliyor" in text
    assert "1 veri yok" in text
    assert "hedef %70" in text


@pytest.mark.asyncio
async def test_format_range_stats_handles_empty_result() -> None:
    text = await format_range_stats(FakeStatsRepository(), "EMPTYUSDT")
    assert text == "📊 EMPTYUSDT için forward sonuç yok."



def test_count_independent_drops_overlapping_requests() -> None:
    from datetime import UTC, datetime, timedelta

    from crypto_signal_engine.db.range_forecast_repository import (
        count_independent,
    )

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
