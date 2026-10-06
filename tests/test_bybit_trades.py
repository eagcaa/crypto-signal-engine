from decimal import Decimal

from crypto_signal_engine.collectors.bybit.trades import (
    BybitFuturesTradeCollector,
    BybitSpotTradeCollector,
    parse_public_trade,
)
from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide


def test_parse_bybit_public_trade() -> None:
    trades = parse_public_trade(
        {
            "topic": "publicTrade.BTCUSDT",
            "ts": 1720000000123,
            "data": [
                {
                    "T": 1720000000123,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "p": "62000.10",
                    "v": "0.250",
                },
                {
                    "T": 1720000000222,
                    "s": "BTCUSDT",
                    "S": "Sell",
                    "p": "62000.00",
                    "v": "0.100",
                },
            ],
        },
        market_type=MarketType.FUTURES,
    )

    assert len(trades) == 2
    assert trades[0].exchange is Exchange.BYBIT
    assert trades[0].market_type is MarketType.FUTURES
    assert trades[0].side is TradeSide.BUY
    assert trades[0].price == Decimal("62000.10")
    assert trades[1].side is TradeSide.SELL


def test_bybit_topics_are_created_for_symbols() -> None:
    spot = BybitSpotTradeCollector(["BTCUSDT", "ETHUSDT"])
    futures = BybitFuturesTradeCollector(["SOLUSDT"])

    assert spot.topics == ("publicTrade.BTCUSDT", "publicTrade.ETHUSDT")
    assert futures.topics == ("publicTrade.SOLUSDT",)
