from crypto_signal_engine.collectors.binance.derivatives import (
    BinanceLiquidationCollector,
)


def test_builds_binance_liquidation_single_public_stream() -> None:
    collector = BinanceLiquidationCollector(["BTCUSDT"])

    assert collector._build_url() == (
        "wss://fstream.binance.com/public/ws/btcusdt@forceOrder"
    )


def test_builds_binance_liquidation_combined_public_stream() -> None:
    collector = BinanceLiquidationCollector(["BTCUSDT", "ETHUSDT"])

    assert collector._build_url() == (
        "wss://fstream.binance.com/public/stream?"
        "streams=btcusdt@forceOrder/ethusdt@forceOrder"
    )
