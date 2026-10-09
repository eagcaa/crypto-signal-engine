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



def test_historical_trade_client_uses_sub_hour_windows() -> None:
    assert (
        BinanceSpotHistoricalTradeClient.MAX_TIME_WINDOW
        < timedelta(hours=1)
    )



def test_historical_trade_client_validates_retry_settings() -> None:
    with pytest.raises(ValueError):
        BinanceSpotHistoricalTradeClient(max_retries=-1)

    with pytest.raises(ValueError):
        BinanceSpotHistoricalTradeClient(retry_backoff_seconds=-0.1)


def test_historical_trade_client_uses_longer_timeout_and_retries() -> None:
    client = BinanceSpotHistoricalTradeClient()

    assert client._timeout.total == 30.0
    assert client._max_retries == 4
    assert client._retry_delay(0) == 1.0
    assert client._retry_delay(3) == 8.0
