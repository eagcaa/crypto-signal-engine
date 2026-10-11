from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
    LiquidatedPositionSide,
    LiquidationEvent,
)
from crypto_signal_engine.domain.models import (
    Exchange,
    MarketType,
    TradeSide,
    TradeTick,
)
from crypto_signal_engine.watch import MarketWatch, WatchConfig


T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _trade(
    minute: float,
    price: float,
    quantity: float,
    side: TradeSide,
    market: MarketType = MarketType.SPOT,
) -> TradeTick:
    return TradeTick(
        exchange=Exchange.BINANCE,
        market_type=market,
        symbol="BTCUSDT",
        event_time=T0 + timedelta(minutes=minute),
        price=Decimal(str(price)),
        quantity=Decimal(str(quantity)),
        side=side,
    )


def _derivatives(
    minute: float,
    open_interest: float,
    funding: str = "0.0001",
) -> ExchangeDerivativesSnapshot:
    return ExchangeDerivativesSnapshot(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        timestamp=T0 + timedelta(minutes=minute),
        open_interest=Decimal(str(open_interest)),
        open_interest_value_usd=None,
        oi_change_5m_pct=None,
        oi_change_15m_pct=None,
        funding_rate=Decimal(funding),
        long_short_ratio=Decimal("1.5"),
        long_account_ratio=None,
        short_account_ratio=None,
        top_trader_long_short_ratio=Decimal("1.2"),
        taker_buy_sell_ratio=None,
        taker_buy_volume=None,
        taker_sell_volume=None,
    )


def _liquidation(
    minute: float,
    side: LiquidatedPositionSide,
    quantity: float,
) -> LiquidationEvent:
    return LiquidationEvent(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        event_time=T0 + timedelta(minutes=minute),
        position_side=side,
        price=Decimal("100000"),
        quantity=Decimal(str(quantity)),
    )


def _price_path(watch: MarketWatch, start: float, end: float) -> None:
    for minute in range(16):
        price = start + (end - start) * minute / 15
        watch.on_trade(
            _trade(
                minute,
                price,
                0.01,
                TradeSide.BUY,
            )
        )


def test_whale_trade_alert_only_above_threshold_and_cooldown() -> None:
    watch = MarketWatch(
        "BTCUSDT",
        WatchConfig(whale_trade_usd=1_000_000),
    )

    assert watch.on_trade(
        _trade(0, 100_000, 5, TradeSide.BUY)
    ) == []

    [alert] = watch.on_trade(
        _trade(
            0,
            100_000,
            12,
            TradeSide.SELL,
            MarketType.FUTURES,
        )
    )
    assert alert.kind.startswith("whale_trade_")
    assert "VADELİ" in alert.text
    assert "SATTI" in alert.text
    assert "$1.2M" in alert.text

    assert watch.on_trade(
        _trade(
            3,
            100_000,
            15,
            TradeSide.SELL,
            MarketType.FUTURES,
        )
    ) == []

    assert watch.on_trade(
        _trade(
            6,
            100_000,
            15,
            TradeSide.SELL,
            MarketType.FUTURES,
        )
    )


def test_positioning_new_longs_when_price_and_oi_rise() -> None:
    watch = MarketWatch("BTCUSDT")
    _price_path(watch, 100_000, 101_000)
    watch.on_derivatives(_derivatives(0, 80_000))
    alerts = watch.on_derivatives(_derivatives(15, 81_600))

    assert [alert.kind for alert in alerts] == [
        "positioning_new_longs"
    ]
    assert "+1.00%" in alerts[0].text
    assert "+2.00%" in alerts[0].text


def test_positioning_short_cover_when_price_rises_and_oi_falls() -> None:
    watch = MarketWatch("BTCUSDT")
    _price_path(watch, 100_000, 101_000)
    watch.on_derivatives(_derivatives(0, 80_000))
    alerts = watch.on_derivatives(_derivatives(15, 78_400))

    assert [alert.kind for alert in alerts] == [
        "positioning_short_cover"
    ]


def test_positioning_needs_full_window_and_minimum_moves() -> None:
    watch = MarketWatch("BTCUSDT")
    _price_path(watch, 100_000, 100_200)
    watch.on_derivatives(_derivatives(0, 80_000))
    assert watch.on_derivatives(_derivatives(15, 81_600)) == []

    early = MarketWatch("BTCUSDT")
    for minute in range(5):
        early.on_trade(
            _trade(
                minute,
                100_000 + minute * 500,
                0.01,
                TradeSide.BUY,
            )
        )
    early.on_derivatives(_derivatives(0, 80_000))
    assert early.on_derivatives(_derivatives(4, 82_000)) == []


def test_liquidation_wave_sums_one_side_within_window() -> None:
    watch = MarketWatch(
        "BTCUSDT",
        WatchConfig(liquidation_wave_usd=5_000_000),
    )
    side = LiquidatedPositionSide.LONG

    assert watch.on_liquidation(_liquidation(0, side, 20)) == []
    assert watch.on_liquidation(_liquidation(1, side, 20)) == []
    [alert] = watch.on_liquidation(_liquidation(2, side, 15))

    assert alert.kind == "liquidation_long"
    assert "$5.5M" in alert.text

    fresh = MarketWatch("BTCUSDT")
    fresh.on_liquidation(_liquidation(0, side, 40))
    assert fresh.on_liquidation(_liquidation(10, side, 20)) == []


def test_funding_extremes() -> None:
    watch = MarketWatch("BTCUSDT")
    [high] = watch.on_derivatives(
        _derivatives(0, 80_000, funding="0.0006")
    )
    assert high.kind == "funding_high"

    other = MarketWatch("BTCUSDT")
    [low] = other.on_derivatives(
        _derivatives(0, 80_000, funding="-0.0002")
    )
    assert low.kind == "funding_negative"

    normal = MarketWatch("BTCUSDT")
    assert normal.on_derivatives(_derivatives(0, 80_000)) == []


def test_futures_led_rally_flagged_when_spot_sells() -> None:
    watch = MarketWatch(
        "BTCUSDT",
        WatchConfig(flow_min_usd=5_000_000),
    )
    _price_path(watch, 100_000, 101_000)
    watch.on_trade(
        _trade(15, 101_000, 60, TradeSide.SELL)
    )
    watch.on_trade(
        _trade(
            15,
            101_000,
            60,
            TradeSide.BUY,
            MarketType.FUTURES,
        )
    )

    kinds = [
        alert.kind
        for alert in watch.tick(T0 + timedelta(minutes=15))
    ]
    assert "flow_futures_led_up" in kinds


def test_spot_led_rally_flagged() -> None:
    watch = MarketWatch(
        "BTCUSDT",
        WatchConfig(flow_min_usd=5_000_000),
    )
    _price_path(watch, 100_000, 101_000)
    watch.on_trade(
        _trade(15, 101_000, 60, TradeSide.BUY)
    )

    kinds = [
        alert.kind
        for alert in watch.tick(T0 + timedelta(minutes=15))
    ]
    assert "flow_spot_led_up" in kinds


def test_hourly_summary_once_per_hour() -> None:
    watch = MarketWatch("BTCUSDT")
    watch.on_trade(
        _trade(0, 100_000, 0.01, TradeSide.BUY)
    )
    watch.on_derivatives(_derivatives(0, 80_000))

    first = watch.tick(T0 + timedelta(minutes=1))
    assert [alert.kind for alert in first] == ["summary"]
    assert "Funding: 0.0100%" in first[0].text

    assert watch.tick(T0 + timedelta(minutes=30)) == []

    later = watch.tick(T0 + timedelta(minutes=61))
    assert [alert.kind for alert in later] == ["summary"]
