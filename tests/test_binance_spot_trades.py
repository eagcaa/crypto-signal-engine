from decimal import Decimal

from crypto_signal_engine.collectors.binance.spot_trades import (
    BinanceSpotTradeCollector,
    parse_agg_trade,
)
from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide


def test_parse_agg_trade_marks_taker_buy() -> None:
    trade = parse_agg_trade(
        {
            "e": "aggTrade",
            "E": 1720000000123,
            "s": "BTCUSDT",
            "p": "62000.10",
            "q": "0.250",
            "m": False,
        }
    )

    assert trade.exchange is Exchange.BINANCE
    assert trade.market_type is MarketType.SPOT
    assert trade.symbol == "BTCUSDT"
    assert trade.price == Decimal("62000.10")
    assert trade.quantity == Decimal("0.250")
    assert trade.side is TradeSide.BUY


def test_parse_agg_trade_marks_taker_sell() -> None:
    trade = parse_agg_trade(
        {
            "e": "aggTrade",
            "E": 1720000000123,
            "s": "ETHUSDT",
            "p": "3500.00",
            "q": "2.5",
            "m": True,
        }
    )

    assert trade.side is TradeSide.SELL


def test_builds_combined_stream_for_multiple_symbols() -> None:
    collector = BinanceSpotTradeCollector(["BTCUSDT", "ETHUSDT"])

    assert collector.stream_names == ("btcusdt@aggTrade", "ethusdt@aggTrade")
    assert collector._build_url() == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@aggTrade/ethusdt@aggTrade"
    )
