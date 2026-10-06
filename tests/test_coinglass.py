from decimal import Decimal

from crypto_signal_engine.integrations.coinglass import CoinGlassClient


def test_base_asset_from_pair() -> None:
    assert CoinGlassClient._base_asset("BTCUSDT") == "BTC"
    assert CoinGlassClient._base_asset("ethusdc") == "ETH"


def test_decimal_or_none() -> None:
    assert CoinGlassClient._decimal_or_none("0.007343") == Decimal("0.007343")
    assert CoinGlassClient._decimal_or_none(None) is None


def test_find_exchange_is_case_insensitive() -> None:
    rows = [
        {"exchange": "BINANCE", "funding_rate": "0.01"},
        {"exchange": "Bybit", "funding_rate": "0.02"},
    ]

    row = CoinGlassClient._find_exchange(rows, "binance")

    assert row is not None
    assert row["funding_rate"] == "0.01"
