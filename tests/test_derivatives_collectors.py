from decimal import Decimal

from crypto_signal_engine.collectors.binance.derivatives import (
    BinanceDerivativesClient,
    BinanceLiquidationCollector,
)
from crypto_signal_engine.collectors.bybit.derivatives import (
    BybitDerivativesClient,
    BybitLiquidationCollector,
)
from crypto_signal_engine.domain.derivatives import LiquidatedPositionSide


def test_binance_open_interest_change() -> None:
    rows = [
        {"sumOpenInterestValue": "100"},
        {"sumOpenInterestValue": "110"},
    ]

    change = BinanceDerivativesClient._history_change_pct(
        rows,
        "sumOpenInterestValue",
    )

    assert change == Decimal("10.0")


def test_bybit_open_interest_change() -> None:
    rows = [
        {"openInterest": "110"},
        {"openInterest": "100"},
    ]

    change = BybitDerivativesClient._history_change_pct(rows)

    assert change == Decimal("10.0")


def test_binance_liquidation_parser() -> None:
    event = BinanceLiquidationCollector.parse_message(
        {
            "E": 1_700_000_000_000,
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",
                "ap": "85000",
                "z": "0.5",
            },
        }
    )

    assert event is not None
    assert event.position_side == LiquidatedPositionSide.LONG
    assert event.notional_usd == Decimal("42500.0")


def test_bybit_liquidation_parser() -> None:
    events = BybitLiquidationCollector.parse_message(
        {
            "data": [
                {
                    "T": 1_700_000_000_000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "0.25",
                    "p": "85000",
                }
            ]
        }
    )

    assert len(events) == 1
    assert events[0].position_side == LiquidatedPositionSide.LONG
    assert events[0].notional_usd == Decimal("21250.00")
