from decimal import Decimal

from crypto_signal_engine.collectors.binance.futures_trades import (
    BinanceFuturesTradeCollector,
    parse_futures_agg_trade,
)
from crypto_signal_engine.domain.models import MarketType, TradeSide


def test_parse_binance_futures_agg_trade() -> None:
    trade = parse_futures_agg_trade(
        {
            "e": "aggTrade",
            "E": 1720000000123,
            "s": "BTCUSDT",
            "p": "62000.10",
            "q": "0.250",
            "m": False,
        }
    )

    assert trade.market_type is MarketType.FUTURES
    assert trade.price == Decimal("62000.10")
    assert trade.side is TradeSide.BUY


def test_builds_binance_futures_combined_stream() -> None:
    collector = BinanceFuturesTradeCollector(["BTCUSDT", "ETHUSDT"])

    assert collector._build_url() == (
        "wss://fstream.binance.com/market/stream?"
        "streams=btcusdt@aggTrade/ethusdt@aggTrade"
    )



def test_builds_binance_futures_single_public_stream() -> None:
    collector = BinanceFuturesTradeCollector(["BTCUSDT"])

    assert collector._build_url() == (
        "wss://fstream.binance.com/market/ws/btcusdt@aggTrade"
    )
