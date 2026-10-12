from datetime import UTC, datetime, timedelta
from math import exp

from crypto_signal_engine.forecast.range import PricePoint, estimate_live_range
from crypto_signal_engine.integrations.telegram_commands import TelegramCommandPoller
from crypto_signal_engine.watch.coin_analysis import normalize_symbol


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_normalize_symbol_accepts_plain_and_usdt() -> None:
    assert normalize_symbol("btc") == "BTCUSDT"
    assert normalize_symbol("PYTHUSDT") == "PYTHUSDT"
    assert normalize_symbol("/analyze pendle") == "PENDLEUSDT"
    assert normalize_symbol("/eth@MyBot") == "ETHUSDT"


def test_command_parser_accepts_plain_ticker() -> None:
    assert TelegramCommandPoller._parse_request("BTC") == "BTC"
    assert TelegramCommandPoller._parse_request("/analyze PYTH") == "PYTH"
    assert TelegramCommandPoller._parse_request("/start") is None
    assert TelegramCommandPoller._parse_request("btc usdt") == "btcusdt"
    assert TelegramCommandPoller._parse_request("/analyze") is None
    assert TelegramCommandPoller._parse_request("/pendle@MyBot") == "pendle"


def test_command_poller_accepts_extra_allowed_chats() -> None:
    poller = TelegramCommandPoller(
        bot_token="token",
        allowed_chat_id="111",
        analyzer=None,
        extra_allowed_chat_ids=("222", " "),
    )
    assert poller._allowed_chat_ids == {"111", "222"}


def test_live_range_uses_only_completed_history() -> None:
    points = [
        PricePoint(
            timestamp=T0 + timedelta(minutes=index),
            price=100.0 * exp(index * 0.0001),
        )
        for index in range(60 * 24 + 1)
    ]
    estimate = estimate_live_range(
        points,
        horizon_minutes=15,
        target_coverage=0.70,
        minimum_calibration_count=20,
        volatility_scaled=False,
    )
    assert estimate is not None
    assert estimate.lower < points[-1].price < estimate.upper
    assert estimate.calibration_count >= 20



def test_coin_analyzer_can_disable_bybit() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(bybit_enabled=False)
    assert analyzer._bybit_enabled is False


def test_coin_analyzer_can_enable_bybit() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(bybit_enabled=True)
    assert analyzer._bybit_enabled is True
