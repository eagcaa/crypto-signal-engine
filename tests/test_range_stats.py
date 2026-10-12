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
            ),
            RangeCoverageStat(
                symbol="BTCUSDT",
                horizon_minutes=60,
                evaluated=4,
                covered=3,
                coverage_pct=Decimal("75"),
                pending=1,
                no_data=1,
            ),
        ]


@pytest.mark.asyncio
async def test_format_range_stats_renders_coverage_and_statuses() -> None:
    text = await format_range_stats(FakeStatsRepository(), "BTCUSDT")

    assert "BTCUSDT" in text
    assert "15 dk: 10 tahmin / %70.0 tuttu (7/10)" in text
    assert "1 saat: 4 tahmin / %75.0 tuttu (3/4)" in text
    assert "2 bekliyor" in text
    assert "1 veri yok" in text
    assert "Hedef coverage: %70" in text


@pytest.mark.asyncio
async def test_format_range_stats_handles_empty_result() -> None:
    text = await format_range_stats(FakeStatsRepository(), "EMPTYUSDT")
    assert text == "📊 EMPTYUSDT için forward sonuç yok."
