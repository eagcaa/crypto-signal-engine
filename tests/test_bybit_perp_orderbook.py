from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_engine.collectors.bybit.perp_orderbook import parse_perp_orderbook


def payload(bid_qty="3", ask_qty="1"):
    return {"retCode": 0, "time": 1760000000000, "result": {
        "s": "BTCUSDT", "ts": 1760000000000,
        "b": [["100", bid_qty]], "a": [["101", ask_qty]]}}


def test_buy_pressure():
    report = parse_perp_orderbook(payload(), "BTCUSDT")
    assert report.pressure == "BUY_PRESSURE"
    assert report.best_bid == Decimal("100")
    assert report.best_ask == Decimal("101")
    assert report.timestamp.tzinfo == UTC


def test_sell_pressure():
    assert parse_perp_orderbook(payload("1", "3"), "BTCUSDT").pressure == "SELL_PRESSURE"


def test_balanced():
    assert parse_perp_orderbook(payload("1", "1"), "BTCUSDT").pressure == "BALANCED"


def test_wrong_symbol():
    with pytest.raises(ValueError):
        parse_perp_orderbook(payload(), "ETHUSDT")


def test_crossed_book():
    data = payload()
    data["result"]["a"] = [["99", "1"]]
    with pytest.raises(ValueError, match="Crossed"):
        parse_perp_orderbook(data, "BTCUSDT")
