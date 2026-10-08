from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_engine.collectors.binance.historical_trades import (
    BinanceSpotHistoricalTradeClient,
)


def test_historical_trade_client_validates_limit() -> None:
    with pytest.raises(ValueError):
        BinanceSpotHistoricalTradeClient(request_limit=0)

    with pytest.raises(ValueError):
        BinanceSpotHistoricalTradeClient(request_limit=1001)


@pytest.mark.asyncio
async def test_historical_trade_client_requires_valid_range() -> None:
    client = BinanceSpotHistoricalTradeClient()
    now = datetime.now(UTC)

    with pytest.raises(ValueError):
        await client.fetch_price_points(
            "BTCUSDT",
            start=now,
            end=now - timedelta(minutes=1),
        )
